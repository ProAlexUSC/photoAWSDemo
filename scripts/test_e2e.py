"""端到端测试：上传照片 → Scheduler 启动状态机 → Step Functions 编排全流程 → 验证 DB"""

import json
import os
import sys
import time

import boto3
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "scheduler", "src"))


def main():
    db_url = os.environ.get("DATABASE_URL", "postgresql://dev:dev@localhost:5433/photo_pipeline")

    # 确保 STATE_MACHINE_ARN 已设置
    os.environ.setdefault(
        "STATE_MACHINE_ARN",
        "arn:aws:states:us-east-1:000000000000:stateMachine:photo-pipeline",
    )

    s3 = boto3.client("s3")
    sfn = boto3.client("stepfunctions")

    # 1. 上传测试照片到 MiniStack S3
    fixture_dir = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")
    s3_keys = []
    for filename in sorted(os.listdir(fixture_dir)):
        if filename.endswith(".jpg"):
            key = f"test/{filename}"
            filepath = os.path.join(fixture_dir, filename)
            with open(filepath, "rb") as f:
                s3.put_object(Bucket="photo-uploads", Key=key, Body=f.read())
            s3_keys.append(key)
            print(f"✅ Uploaded {key}")

    if not s3_keys:
        print("❌ No test fixtures found", file=sys.stderr)
        sys.exit(1)

    # 2. 调用 Scheduler（启动状态机）
    from scheduler.handler import handler

    request_id = f"e2e-test-{int(time.time())}"
    print(f"\n📞 Calling scheduler with {len(s3_keys)} photos...")
    result = handler({"request_id": request_id, "user_id": 1, "s3_keys": s3_keys}, None)
    body = json.loads(result["body"])
    batch_id = body["batch_id"]
    print(f"✅ Scheduler returned batch_id={batch_id}")

    # 3. 等待状态机执行完成
    state_machine_arn = os.environ["STATE_MACHINE_ARN"]
    print("\n⏳ Waiting for Step Functions execution...")

    # 找到最新的执行
    time.sleep(2)
    executions = sfn.list_executions(stateMachineArn=state_machine_arn)
    if not executions.get("executions"):
        print("❌ No executions found", file=sys.stderr)
        sys.exit(1)

    execution_arn = executions["executions"][0]["executionArn"]
    print(f"  Execution: {execution_arn}")

    for i in range(120):
        resp = sfn.describe_execution(executionArn=execution_arn)
        status = resp["status"]
        if status in ("SUCCEEDED", "FAILED", "TIMED_OUT", "ABORTED"):
            break
        if i % 10 == 0:
            print(f"  Status: {status} ({i * 2}s elapsed)")
        time.sleep(2)

    print(f"  Final status: {status}")
    if status != "SUCCEEDED":
        print("❌ Execution failed!", file=sys.stderr)
        if "error" in resp:
            print(f"  Error: {resp['error']}", file=sys.stderr)
        if "cause" in resp:
            print(f"  Cause: {resp['cause']}", file=sys.stderr)
        sys.exit(1)
    print("✅ State machine completed")

    # 4. 验证数据库
    print("\n🔍 Verifying database...")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    # batch status
    cur.execute("SELECT status FROM photo_batches WHERE batch_id = %s", (batch_id,))
    batch = cur.fetchone()
    print(f"  Batch status: {batch[0]}")
    assert batch[0] == "completed", f"Expected 'completed', got '{batch[0]}'"

    # photos completed + face_count
    cur.execute(
        "SELECT COUNT(*), SUM(COALESCE(face_count, 0)) "
        "FROM photos WHERE batch_id = %s AND status = 'completed'",
        (batch_id,),
    )
    photos = cur.fetchone()
    print(f"  Photos: {photos[0]} completed, {photos[1] or 0} total faces")
    assert photos[0] == len(s3_keys), f"Expected {len(s3_keys)} completed, got {photos[0]}"

    # tags written by Stage 2
    cur.execute(
        "SELECT COUNT(*) FROM photos WHERE batch_id = %s AND tags IS NOT NULL",
        (batch_id,),
    )
    tagged = cur.fetchone()[0]
    print(f"  Tagged: {tagged}/{len(s3_keys)}")
    assert tagged == len(s3_keys), f"Expected {len(s3_keys)} tagged, got {tagged}"

    # vlm_result written by Stage 3
    cur.execute(
        "SELECT COUNT(*) FROM photos WHERE batch_id = %s AND vlm_result IS NOT NULL",
        (batch_id,),
    )
    vlm_done = cur.fetchone()[0]
    print(f"  VLM extracted: {vlm_done}/{len(s3_keys)}")
    assert vlm_done == len(s3_keys), f"Expected {len(s3_keys)} VLM done, got {vlm_done}"

    # face embeddings
    cur.execute(
        "SELECT COUNT(*) FROM face_embeddings fe "
        "JOIN photos p ON fe.photo_id = p.photo_id "
        "WHERE p.batch_id = %s",
        (batch_id,),
    )
    embedding_count = cur.fetchone()[0]
    if embedding_count > 0:
        print(f"  ✅ {embedding_count} face embeddings written")
    else:
        print("  ⚠️  No face embeddings (test images may not contain detectable faces)")

    conn.close()
    print("\n🎉 E2E test passed! Full pipeline: Worker → Tagger → VLM → Complete")


if __name__ == "__main__":
    main()
