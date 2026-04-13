"""启动 MiniStack 后运行一次，创建本地 AWS 资源 + 部署 Lambda Container Image"""
import subprocess
import sys
import time

import boto3


def wait_for_ministack(max_retries: int = 30):
    s3 = boto3.client("s3")
    for i in range(max_retries):
        try:
            s3.list_buckets()
            print("✅ MiniStack is ready")
            return
        except Exception:
            print(f"⏳ Waiting for MiniStack... ({i + 1}/{max_retries})")
            time.sleep(1)
    print("❌ MiniStack failed to start", file=sys.stderr)
    sys.exit(1)


def setup():
    wait_for_ministack()

    s3 = boto3.client("s3")
    sqs = boto3.client("sqs")
    lam = boto3.client("lambda")

    # 1. 创建 S3 Bucket
    try:
        s3.create_bucket(Bucket="photo-uploads")
        print("✅ S3 bucket 'photo-uploads' created")
    except s3.exceptions.BucketAlreadyExists:
        print("ℹ️  S3 bucket 'photo-uploads' already exists")

    # 2. 创建 SQS DLQ
    try:
        sqs.create_queue(QueueName="scheduler-dlq")
        print("✅ SQS DLQ 'scheduler-dlq' created")
    except Exception:
        print("ℹ️  SQS DLQ 'scheduler-dlq' already exists")

    # 3. 部署 Lambda（Container Image 模式）
    try:
        lam.create_function(
            FunctionName="photo-scheduler",
            PackageType="Image",
            Role="arn:aws:iam::000000000000:role/lambda-role",
            Code={"ImageUri": "photo-scheduler:latest"},
            Environment={
                "Variables": {
                    "LOCAL_DEV": "true",
                    "DATABASE_URL": "postgresql://dev:dev@host.docker.internal:5432/photo_pipeline",
                    "AWS_ENDPOINT_URL": "http://host.docker.internal:4566",
                    "AWS_ACCESS_KEY_ID": "test",
                    "AWS_SECRET_ACCESS_KEY": "test",
                    "AWS_DEFAULT_REGION": "us-east-1",
                }
            },
            Timeout=30,
        )
        print("✅ Lambda 'photo-scheduler' deployed (Container Image)")
    except lam.exceptions.ResourceConflictException:
        lam.update_function_code(
            FunctionName="photo-scheduler",
            ImageUri="photo-scheduler:latest",
        )
        print("ℹ️  Lambda 'photo-scheduler' updated")


if __name__ == "__main__":
    setup()
