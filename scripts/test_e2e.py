"""端到端测试（MiniStack）：S3 → Scheduler Lambda → docker run worker → SFN → DB 校验。

MiniStack 两个限制需要 workaround：
- `ecs:runTask.sync` 不真跑容器且不自动 STOPPED → 解锁：`ecs.stop_task`
- Worker 不在 SFN 跑，`face_embeddings` 空 → 预填：`docker compose run worker`

Scheduler 的 `sfn.start_execution` 是异步的，返回后 SFN 在后台 hang 在 RunWorker；
这段时间本脚本 `docker compose run worker` 填数据（~40-60s），之后 stop_task 解锁，
SFN 继续 GetPhotoIds → Tag/VLM Map → MarkComplete。

Worker span 不嵌套在 photo_pipeline 下（Scheduler 未把 observation_id 透给调用方），
只共享 trace_id 作为同 trace 的顶层 sibling。
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

    # 2. 调 Scheduler Lambda
    request_id = f"e2e-test-{int(time.time())}"
    user_id = 1
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

    print("\n🔧 Worker — face detection + embeddings (docker compose run)...")
    worker_env = ["-e", f"BATCH_ID={batch_id}", "-e", f"S3_KEYS={json.dumps(s3_keys)}"]
    if trace_id:
        worker_env += ["-e", f"LANGFUSE_TRACE_ID={trace_id}"]
    r = subprocess.run(
        ["docker", "compose", "run", "--rm", *worker_env, "worker"],
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
