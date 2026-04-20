"""端到端测试（MiniStack Plan A）：走 Scheduler Lambda 的真·e2e。

覆盖 Scheduler→SFN 契约 + propagate_attributes(user_id/session_id/tags)。
流程：S3 upload → Scheduler Lambda(invoke) → docker run worker
      → stop_task 解锁 → 等 SFN 终态 → 验证 DB。

MiniStack 两处限制影响 e2e：
1. `ecs:runTask.sync` 不真跑容器，只在 ECS 里注册一条 RUNNING task 记录；且不会自动
   转为 STOPPED，SFN 的 `.sync` 会无限 block。workaround：Scheduler 触发 SFN 后主动
   `ecs.stop_task`，SFN 收到状态转移事件即继续。
2. 即便解锁，Worker 容器没真跑，face_embeddings 表是空的，GetPhotoIds 拿不到 photo_id。
   workaround：在解锁 ECS 之前，先用 `docker compose run worker` 自己把 DB 填好。

时序安排上两者兼容：Scheduler 内部 `sfn.start_execution` 是异步返回 executionArn，SFN 后台
开始 RunWorker 即刻 hang 在 .sync；这段时间 Python 脚本 `docker compose run worker`
populate 真数据（~40-60s），之后 stop_task 解锁，SFN 继续推进到 GetPhotoIds，拿到真
photo_id 完成后续 Tag/VLM/MarkComplete。

Trace 说明：Scheduler `@observe(name="photo_pipeline")` 是 trace root；Worker 这次拿不到
parent_obs_id（Scheduler handler 未把 observation_id 返回给 invoke 调用方），Worker span
作为 trace 顶层 sibling 出现，不嵌套在 photo_pipeline 下面。这是可接受的 trade-off——
Codex adversarial review finding #3 的核心是"e2e 走真实 Scheduler"，已达成；
propagate_attributes(user_id, session_id, tags) 和 Scheduler→SFN 契约都在 e2e 覆盖下了。

覆盖：Scheduler→SFN 契约、propagate_attributes、SFN JSON 结构、Map 并发、
     ResultPath/ResultSelector、Lambda event 契约、DB 写入。
不覆盖：RunWorker 的 ECS 参数传递（留给真 AWS Batch smoke 另起独立验证）。
"""

import json
import os
import subprocess
import sys
import time

import boto3
import psycopg2

STATE_MACHINE_ARN = "arn:aws:states:us-east-1:000000000000:stateMachine:photo-pipeline"
ECS_CLUSTER = "photo-pipeline"
SCHEDULER_FUNCTION = "photo-scheduler"
EXECUTION_TIMEOUT_SEC = 180
POLL_INTERVAL_SEC = 1
ECS_UNLOCK_TIMEOUT_SEC = 15


def _unlock_ministack_ecs(ecs_client) -> None:
    """MiniStack workaround: stop_task 解锁 SFN 里的 ecs:runTask.sync。

    SFN start_execution 后，它会调 `ecs:runTask` 注册一个 RUNNING task，
    然后在 .sync 语义下阻塞等该 task 转为 STOPPED。MiniStack 不会真跑容器也不会自动
    转态，所以我们主动 stop 掉 task。
    """
    deadline = time.time() + ECS_UNLOCK_TIMEOUT_SEC
    while time.time() < deadline:
        tasks = ecs_client.list_tasks(cluster=ECS_CLUSTER, desiredStatus="RUNNING")
        arns = tasks.get("taskArns", [])
        if arns:
            for arn in arns:
                ecs_client.stop_task(cluster=ECS_CLUSTER, task=arn, reason="ministack-local-fake")
            print(f"  🔓 unlocked {len(arns)} ECS task(s) (ministack workaround)")
            return
        time.sleep(0.5)
    raise TimeoutError(
        f"No ECS task appeared in cluster {ECS_CLUSTER!r} within {ECS_UNLOCK_TIMEOUT_SEC}s"
    )


def _wait_for_execution(sfn, execution_arn: str) -> dict:
    """轮询 describe_execution 直到终态；失败时打印历史帮 debug。"""
    deadline = time.time() + EXECUTION_TIMEOUT_SEC
    last_status = None
    while time.time() < deadline:
        desc = sfn.describe_execution(executionArn=execution_arn)
        status = desc["status"]
        if status != last_status:
            print(f"  SFN: {status}", flush=True)
            last_status = status
        if status in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
            if status != "SUCCEEDED":
                print("\n--- execution history (last 20 events) ---", file=sys.stderr)
                hist = sfn.get_execution_history(
                    executionArn=execution_arn, maxResults=20, reverseOrder=True
                )
                for ev in hist["events"]:
                    print(f"  [{ev['type']}] id={ev.get('id')}", file=sys.stderr)
                    for k in (
                        "taskFailedEventDetails",
                        "executionFailedEventDetails",
                        "lambdaFunctionFailedEventDetails",
                    ):
                        if k in ev:
                            print(f"    {k}: {ev[k]}", file=sys.stderr)
            return desc
        time.sleep(POLL_INTERVAL_SEC)
    raise TimeoutError(f"SFN execution timed out after {EXECUTION_TIMEOUT_SEC}s")


def main():
    db_url = os.environ.get("DATABASE_URL", "postgresql://dev:dev@localhost:5433/photo_pipeline")
    os.environ.setdefault("DATABASE_URL", db_url)

    s3 = boto3.client("s3")
    sfn = boto3.client("stepfunctions")
    ecs = boto3.client("ecs")
    lam = boto3.client("lambda")

    # 1. 上传 S3 fixtures
    fixture_dir = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")
    s3_keys = []
    for filename in sorted(os.listdir(fixture_dir)):
        if filename.endswith(".jpg"):
            key = f"test/{filename}"
            with open(os.path.join(fixture_dir, filename), "rb") as f:
                s3.put_object(Bucket="photo-uploads", Key=key, Body=f.read())
            s3_keys.append(key)
            print(f"✅ Uploaded {key}")

    if not s3_keys:
        print("❌ No test fixtures found", file=sys.stderr)
        sys.exit(1)

    # 2. 调 Scheduler Lambda —— 覆盖 Scheduler→SFN 契约 + propagate_attributes
    request_id = f"e2e-test-{int(time.time())}"
    user_id = 1  # batch_manager 侧 user_id 是 int；Scheduler 会把它 str() 后挂到 trace attributes
    print(f"\n🚀 Invoking Scheduler Lambda ({SCHEDULER_FUNCTION})...")
    invoke_resp = lam.invoke(
        FunctionName=SCHEDULER_FUNCTION,
        InvocationType="RequestResponse",
        Payload=json.dumps(
            {
                "request_id": request_id,
                "user_id": user_id,
                "s3_keys": s3_keys,
            }
        ).encode(),
    )
    raw = invoke_resp["Payload"].read().decode()
    if invoke_resp.get("FunctionError"):
        print(f"❌ Scheduler invoke FunctionError: {raw}", file=sys.stderr)
        sys.exit(1)
    resp = json.loads(raw)
    if resp.get("statusCode") != 200:
        print(f"❌ Scheduler returned non-200: {resp}", file=sys.stderr)
        sys.exit(1)
    body = resp["body"] if isinstance(resp.get("body"), dict) else json.loads(resp["body"])
    batch_id = body["batch_id"]
    execution_arn = body["execution_arn"]
    # Scheduler 返回 langfuse_trace_id 便于外部挂 child span / 展示同 trace URL
    trace_id = body.get("langfuse_trace_id", "")
    print(f"✅ batch_id={batch_id}")
    print(f"✅ execution_arn={execution_arn}")
    if trace_id:
        print(f"🔭 Langfuse trace_id: {trace_id}")

    # 3. Worker populate DB —— 和 SFN 的 RunWorker.sync hang 并行进行
    # NOTE: LANGFUSE_PARENT_OBS_ID 这次不传（Scheduler handler 没把 observation_id 透给
    # 外部 invoke 调用方），Worker span 会作为 trace 顶层 sibling 出现而不是 nested child。
    # 传 LANGFUSE_TRACE_ID 保证 Worker span 至少挂到同一个 trace 上。
    print("\n🔧 Worker — face detection + embeddings (docker compose run)...")
    r = subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "-e",
            f"BATCH_ID={batch_id}",
            "-e",
            f"S3_KEYS={json.dumps(s3_keys)}",
            "-e",
            f"LANGFUSE_TRACE_ID={trace_id}",
            "-e",
            "LANGFUSE_PARENT_OBS_ID=",
            "worker",
        ],
        capture_output=True,
        text=True,
    )
    print(r.stdout)
    if r.returncode != 0:
        print(f"❌ Worker failed:\n{r.stderr}", file=sys.stderr)
        sys.exit(1)
    print("✅ Worker completed")

    # 4. 解锁 SFN RunWorker.sync，再轮询到终态
    _unlock_ministack_ecs(ecs)
    d = _wait_for_execution(sfn, execution_arn)
    if d["status"] != "SUCCEEDED":
        print(f"❌ SFN {d['status']}", file=sys.stderr)
        sys.exit(1)
    print("✅ SFN execution SUCCEEDED")

    # 5. 验证 DB
    print("\n🔍 Verifying database...")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    cur.execute("SELECT status FROM photo_batches WHERE batch_id = %s", (batch_id,))
    batch_status = cur.fetchone()[0]
    print(f"  Batch status: {batch_status}")
    assert batch_status == "completed", f"Expected 'completed', got {batch_status!r}"

    cur.execute(
        "SELECT COUNT(*), SUM(COALESCE(face_count, 0)) "
        "FROM photos WHERE batch_id = %s AND status = 'completed'",
        (batch_id,),
    )
    n_completed, total_faces = cur.fetchone()
    print(f"  Photos: {n_completed}/{len(s3_keys)} completed, {total_faces or 0} total faces")
    assert n_completed == len(s3_keys)

    cur.execute("SELECT COUNT(*) FROM photos WHERE batch_id = %s AND tags IS NOT NULL", (batch_id,))
    tagged = cur.fetchone()[0]
    print(f"  Tagged: {tagged}/{len(s3_keys)}")
    assert tagged == len(s3_keys)

    cur.execute(
        "SELECT COUNT(*) FROM photos WHERE batch_id = %s AND vlm_result IS NOT NULL", (batch_id,)
    )
    vlm_done = cur.fetchone()[0]
    print(f"  VLM extracted: {vlm_done}/{len(s3_keys)}")
    assert vlm_done == len(s3_keys)

    cur.execute(
        "SELECT COUNT(*) FROM face_embeddings fe "
        "JOIN photos p ON fe.photo_id = p.photo_id "
        "WHERE p.batch_id = %s",
        (batch_id,),
    )
    emb = cur.fetchone()[0]
    if emb > 0:
        print(f"  ✅ {emb} face embeddings written")
    else:
        print("  ⚠️  No face embeddings (test images may not contain detectable faces)")

    conn.close()

    # 6. 打印 Langfuse trace URL（Scheduler Lambda / Worker 各自 flush 自己的 span；
    #    e2e 脚本本身不再创建 span，无需 langfuse.flush()）
    if trace_id:
        langfuse_host = os.environ.get("LANGFUSE_HOST") or os.environ.get("LANGFUSE_BASE_URL", "")
        if langfuse_host:
            print(f"\n🔭 Langfuse trace: {langfuse_host.rstrip('/')}/trace/{trace_id}")

    print("\n🎉 E2E test passed! Full SFN execution:")
    print("   Scheduler(Lambda) → Worker(docker) → SFN(RunWorker fake → GetPhotoIds")
    print("                → TagPhotos Map → VLMExtract Map → MarkComplete) → DB verified")


if __name__ == "__main__":
    main()
