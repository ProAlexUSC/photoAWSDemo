"""端到端测试：Scheduler → Worker(docker) → Step Functions(Lambda 链) → 验证 DB"""

import json
import os
import subprocess
import sys
import time

import boto3
import psycopg2

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "services", "scheduler", "src"))


def main():
    db_url = os.environ.get("DATABASE_URL", "postgresql://dev:dev@localhost:5433/photo_pipeline")

    os.environ.setdefault(
        "STATE_MACHINE_ARN",
        "arn:aws:states:us-east-1:000000000000:stateMachine:photo-pipeline",
    )

    s3 = boto3.client("s3")
    lam = boto3.client("lambda")

    # 1. 上传测试照片到 S3
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

    # 2. Scheduler: 创建 batch（直接调用，因为 MiniStack Container Image Lambda 有 bug）
    from scheduler.handler import handler as scheduler_handler

    request_id = f"e2e-test-{int(time.time())}"
    print(f"\n📞 Step 1: Scheduler — creating batch with {len(s3_keys)} photos...")
    result = scheduler_handler({"request_id": request_id, "user_id": 1, "s3_keys": s3_keys}, None)
    body = json.loads(result["body"])
    batch_id = body["batch_id"]
    print(f"✅ batch_id={batch_id}")

    # 3. Worker: docker compose run（ECS RunTask 在 MiniStack 上不真正执行容器）
    print("\n🔧 Step 2: Worker — face detection + embeddings (docker compose run)...")
    worker_result = subprocess.run(
        [
            "docker",
            "compose",
            "run",
            "--rm",
            "-e",
            f"BATCH_ID={batch_id}",
            "-e",
            f"S3_KEYS={json.dumps(s3_keys)}",
            "worker",
        ],
        capture_output=True,
        text=True,
    )
    print(worker_result.stdout)
    if worker_result.returncode != 0:
        print(f"❌ Worker failed:\n{worker_result.stderr}", file=sys.stderr)
        sys.exit(1)
    print("✅ Worker completed")

    # 4. 后续 Pipeline 通过 MiniStack Lambda invoke（模拟 Step Functions Map + Lambda）
    print("\n📋 Step 3: GetPhotoIds via MiniStack Lambda...")
    resp = lam.invoke(FunctionName="get-photo-ids", Payload=json.dumps({"batch_id": batch_id}))
    ids_result = json.loads(resp["Payload"].read())
    photo_ids = ids_result["photo_ids"]
    print(f"✅ Found {len(photo_ids)} photos: {photo_ids}")

    print(f"\n🏷️  Step 4: Tagger — tagging {len(photo_ids)} photos via MiniStack Lambda...")
    for pid in photo_ids:
        resp = lam.invoke(FunctionName="photo-tagger", Payload=json.dumps({"photo_id": pid}))
        tag_result = json.loads(resp["Payload"].read())
        assert tag_result["status"] == "tagged", f"Tagger failed for {pid}: {tag_result}"
    print(f"✅ Tagged {len(photo_ids)} photos")

    print(f"\n🤖 Step 5: VLM — extracting {len(photo_ids)} photos via MiniStack Lambda...")
    for pid in photo_ids:
        resp = lam.invoke(FunctionName="photo-vlm", Payload=json.dumps({"photo_id": pid}))
        vlm_result = json.loads(resp["Payload"].read())
        assert vlm_result["status"] == "extracted", f"VLM failed for {pid}: {vlm_result}"
    print(f"✅ Extracted {len(photo_ids)} photos")

    print("\n✓  Step 6: MarkComplete via MiniStack Lambda...")
    resp = lam.invoke(
        FunctionName="photo-mark-complete", Payload=json.dumps({"batch_id": batch_id})
    )
    complete_result = json.loads(resp["Payload"].read())
    assert complete_result["status"] == "completed", f"MarkComplete failed: {complete_result}"
    print("✅ Batch marked complete")

    # 5. 验证数据库
    print("\n🔍 Verifying database...")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    cur.execute("SELECT status FROM photo_batches WHERE batch_id = %s", (batch_id,))
    batch_row = cur.fetchone()
    print(f"  Batch status: {batch_row[0]}")
    assert batch_row[0] == "completed", f"Expected 'completed', got '{batch_row[0]}'"

    cur.execute(
        "SELECT COUNT(*), SUM(COALESCE(face_count, 0)) "
        "FROM photos WHERE batch_id = %s AND status = 'completed'",
        (batch_id,),
    )
    photos = cur.fetchone()
    print(f"  Photos: {photos[0]} completed, {photos[1] or 0} total faces")
    assert photos[0] == len(s3_keys)

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
    embedding_count = cur.fetchone()[0]
    if embedding_count > 0:
        print(f"  ✅ {embedding_count} face embeddings written")
    else:
        print("  ⚠️  No face embeddings (test images may not contain detectable faces)")

    conn.close()
    print("\n🎉 E2E test passed! Full pipeline:")
    print("   Scheduler → Worker(Docker) → GetPhotoIds → Tagger(×N) → VLM(×N) → MarkComplete")
    print("   (Lambda stages run via MiniStack Warm Workers)")


if __name__ == "__main__":
    main()
