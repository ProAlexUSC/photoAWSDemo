"""端到端测试：直接串行调用各 handler，验证完整多阶段 Pipeline"""

import json
import os
import subprocess
import sys
import time

import boto3
import psycopg2

# 让所有 service handler 可被直接 import
for service_dir in ["scheduler", "get_photo_ids", "tagger", "vlm_extractor", "mark_complete"]:
    sys.path.insert(
        0, os.path.join(os.path.dirname(__file__), "..", "services", service_dir, "src")
    )


def main():
    db_url = os.environ.get("DATABASE_URL", "postgresql://dev:dev@localhost:5433/photo_pipeline")
    s3 = boto3.client("s3")

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

    # 2. Scheduler: 创建 batch + photos（直接调用，不走 SFN）
    from scheduler.handler import handler as scheduler_handler

    os.environ.setdefault(
        "STATE_MACHINE_ARN",
        "arn:aws:states:us-east-1:000000000000:stateMachine:photo-pipeline",
    )
    request_id = f"e2e-test-{int(time.time())}"
    print(f"\n📞 Step 1: Scheduler — creating batch with {len(s3_keys)} photos...")
    result = scheduler_handler({"request_id": request_id, "user_id": 1, "s3_keys": s3_keys}, None)
    body = json.loads(result["body"])
    batch_id = body["batch_id"]
    print(f"✅ batch_id={batch_id}")

    # 3. Worker: 人脸检测 + embedding（docker compose run）
    print("\n🔧 Step 2: Worker — face detection + embeddings...")
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

    # 4. GetPhotoIds: 查询 photo_ids
    from get_photo_ids.handler import handler as get_ids_handler

    print("\n📋 Step 3: GetPhotoIds...")
    ids_result = get_ids_handler({"batch_id": batch_id}, None)
    photo_ids = ids_result["photo_ids"]
    print(f"✅ Found {len(photo_ids)} photos: {photo_ids}")

    # 5. Tagger: 每张照片打标（模拟 Map 并行）
    from tagger.handler import handler as tagger_handler

    print(f"\n🏷️  Step 4: Tagger — tagging {len(photo_ids)} photos...")
    for pid in photo_ids:
        tagger_handler({"photo_id": pid}, None)
    print(f"✅ Tagged {len(photo_ids)} photos")

    # 6. VLM Extractor: 每张照片 VLM 提取（模拟 Map 并行）
    from vlm_extractor.handler import handler as vlm_handler

    print(f"\n🤖 Step 5: VLM Extractor — extracting {len(photo_ids)} photos...")
    for pid in photo_ids:
        vlm_handler({"photo_id": pid}, None)
    print(f"✅ Extracted {len(photo_ids)} photos")

    # 7. MarkComplete: 标记 batch 完成
    from mark_complete.handler import handler as complete_handler

    print("\n✓  Step 6: MarkComplete...")
    complete_handler({"batch_id": batch_id}, None)
    print("✅ Batch marked complete")

    # 8. 验证数据库
    print("\n🔍 Verifying database...")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    cur.execute("SELECT status FROM photo_batches WHERE batch_id = %s", (batch_id,))
    batch = cur.fetchone()
    print(f"  Batch status: {batch[0]}")
    assert batch[0] == "completed", f"Expected 'completed', got '{batch[0]}'"

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
    print("\n🎉 E2E test passed! Full pipeline: Scheduler → Worker → Tagger → VLM → Complete")


if __name__ == "__main__":
    main()
