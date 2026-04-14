"""启动 MiniStack 后运行一次，创建本地 AWS 资源 + 部署 Lambda"""

import io
import os
import sys
import time
import zipfile

import boto3


def _build_lambda_zip(service_name):
    """打包 Lambda 代码为 zip（service src + common src + site-packages 依赖）"""
    root = os.path.join(os.path.dirname(__file__), "..")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        # service 源码
        src_dir = os.path.join(root, "services", service_name, "src")
        for dirpath, _, filenames in os.walk(src_dir):
            for fname in filenames:
                if fname.endswith(".py"):
                    full = os.path.join(dirpath, fname)
                    arcname = os.path.relpath(full, src_dir)
                    zf.write(full, arcname)
        # common 源码
        common_dir = os.path.join(root, "packages", "common", "src")
        for dirpath, _, filenames in os.walk(common_dir):
            for fname in filenames:
                if fname.endswith(".py"):
                    full = os.path.join(dirpath, fname)
                    arcname = os.path.relpath(full, common_dir)
                    zf.write(full, arcname)
        # 依赖靠 MiniStack 容器预装（docker/ministack.Dockerfile）
    buf.seek(0)
    return buf.read()


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

    # 3. 部署所有 Lambda（Zip 模式，MiniStack Warm Workers）
    lambda_env = {
        "Variables": {
            "LOCAL_DEV": "true",
            "DATABASE_URL": "postgresql://dev:dev@postgres:5432/photo_pipeline",
            "AWS_ENDPOINT_URL": "http://ministack:4566",
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_DEFAULT_REGION": "us-east-1",
            "STATE_MACHINE_ARN": (
                "arn:aws:states:us-east-1:000000000000:stateMachine:photo-pipeline"
            ),
        }
    }

    all_lambdas = [
        ("photo-scheduler", "scheduler", "scheduler.handler.handler"),
        ("get-photo-ids", "get_photo_ids", "get_photo_ids.handler.handler"),
        ("photo-tagger", "tagger", "tagger.handler.handler"),
        ("photo-vlm", "vlm_extractor", "vlm_extractor.handler.handler"),
        ("photo-mark-complete", "mark_complete", "mark_complete.handler.handler"),
    ]

    for func_name, service_dir, handler in all_lambdas:
        zip_bytes = _build_lambda_zip(service_dir)
        try:
            lam.create_function(
                FunctionName=func_name,
                Runtime="python3.12",
                Role="arn:aws:iam::000000000000:role/lambda-role",
                Handler=handler,
                Code={"ZipFile": zip_bytes},
                Environment=lambda_env,
                Timeout=30,
            )
            print(f"✅ Lambda '{func_name}' deployed (Zip)")
        except lam.exceptions.ResourceConflictException:
            lam.update_function_code(
                FunctionName=func_name,
                ZipFile=zip_bytes,
            )
            print(f"ℹ️  Lambda '{func_name}' updated")

    # 4. 创建 ECS 集群 + 注册 Task Definition
    ecs = boto3.client("ecs")
    try:
        ecs.create_cluster(clusterName="photo-pipeline")
        print("✅ ECS cluster 'photo-pipeline' created")
    except Exception:
        print("ℹ️  ECS cluster 'photo-pipeline' already exists")

    ecs.register_task_definition(
        family="photo-worker",
        containerDefinitions=[
            {
                "name": "worker",
                "image": "photo-worker:latest",
                "essential": True,
                "memory": 2048,
            }
        ],
    )
    print("✅ ECS task definition 'photo-worker' registered")

    # 5. 创建 Step Functions 状态机
    sfn = boto3.client("stepfunctions")
    sfn_path = os.path.join(
        os.path.dirname(__file__), "..", "state-machines", "pipeline-local.json"
    )
    with open(sfn_path) as f:
        definition = f.read()
    try:
        sfn.create_state_machine(
            name="photo-pipeline",
            definition=definition,
            roleArn="arn:aws:iam::000000000000:role/sfn-role",
        )
        print("✅ Step Functions state machine 'photo-pipeline' created")
    except Exception:
        sfn.update_state_machine(
            stateMachineArn="arn:aws:states:us-east-1:000000000000:stateMachine:photo-pipeline",
            definition=definition,
        )
        print("ℹ️  Step Functions state machine 'photo-pipeline' updated")


if __name__ == "__main__":
    setup()
