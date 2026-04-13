"""端到端测试：上传照片 → 调用 Lambda → 运行 Worker → 验证 DB"""
import json
import os
import subprocess
import sys
import time

import boto3
import psycopg2


def main():
    db_url = os.environ.get(
        "DATABASE_URL", "postgresql://dev:dev@localhost:5433/photo_pipeline"
    )

    s3 = boto3.client("s3")
    lam = boto3.client("lambda")

    # 1. 上传测试照片到 MiniStack S3
    fixture_dir = os.path.join(os.path.dirname(__file__), "..", "tests", "fixtures")
    s3_keys = []
    for filename in sorted(os.listdir(fixture_dir)):
        if filename.endswith(".jpg"):
            key = f"test/{filename}"
            filepath = os.path.join(fixture_dir, filename)
            s3.put_object(
                Bucket="photo-uploads",
                Key=key,
                Body=open(filepath, "rb").read(),
            )
            s3_keys.append(key)
            print(f"✅ Uploaded {key}")

    if not s3_keys:
        print("❌ No test fixtures found in tests/fixtures/", file=sys.stderr)
        sys.exit(1)

    # 2. 调用 MiniStack Lambda
    print(f"\n📞 Invoking Lambda with {len(s3_keys)} photos...")
    resp = lam.invoke(
        FunctionName="photo-scheduler",
        Payload=json.dumps({
            "request_id": f"e2e-test-{int(time.time())}",
            "user_id": 1,
            "s3_keys": s3_keys,
        }),
    )
    payload = json.loads(resp["Payload"].read())
    print(f"Lambda response: {payload}")

    if isinstance(payload, dict) and "body" in payload:
        body = json.loads(payload["body"])
    else:
        body = payload
    batch_id = body["batch_id"]
    print(f"✅ Lambda returned batch_id={batch_id}")

    # 3. Docker 容器运行 Worker（CPU 模式）
    print("\n🔧 Running Worker container...")
    result = subprocess.run(
        [
            "docker", "compose", "run", "--rm",
            "-e", f"BATCH_ID={batch_id}",
            "-e", f"S3_KEYS={json.dumps(s3_keys)}",
            "worker",
        ],
        capture_output=True,
        text=True,
    )
    print(result.stdout)
    if result.returncode != 0:
        print(f"❌ Worker failed:\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    print("✅ Worker completed")

    # 4. 验证数据库
    print("\n🔍 Verifying database...")
    conn = psycopg2.connect(db_url)
    cur = conn.cursor()

    cur.execute(
        "SELECT status, total, completed FROM photo_batches WHERE batch_id = %s",
        (batch_id,),
    )
    batch = cur.fetchone()
    print(f"  Batch: status={batch[0]}, total={batch[1]}, completed={batch[2]}")
    assert batch[0] == "completed", f"Expected batch status 'completed', got '{batch[0]}'"

    cur.execute(
        "SELECT COUNT(*), SUM(COALESCE(face_count, 0)) FROM photos WHERE batch_id = %s AND status = 'completed'",
        (batch_id,),
    )
    photos = cur.fetchone()
    print(f"  Photos: {photos[0]} completed, {photos[1] or 0} total faces")
    assert photos[0] == len(s3_keys), f"Expected {len(s3_keys)} completed photos, got {photos[0]}"

    cur.execute(
        """SELECT COUNT(*)
           FROM face_embeddings fe
           JOIN photos p ON fe.photo_id = p.photo_id
           WHERE p.batch_id = %s""",
        (batch_id,),
    )
    embedding_count = cur.fetchone()[0]

    if embedding_count > 0:
        print(f"  ✅ {embedding_count} face embeddings written")
    else:
        print("  ⚠️  No face embeddings (synthetic test images may not contain detectable faces)")

    conn.close()
    print("\n🎉 E2E test passed!")


if __name__ == "__main__":
    main()
