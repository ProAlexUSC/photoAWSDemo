"""启动 MiniStack 后运行一次，创建本地 AWS 资源 + 部署 Lambda Container Image"""

import os
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
                    "DATABASE_URL": "postgresql://dev:dev@host.docker.internal:5433/photo_pipeline",
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

    # 5. 部署 4 个新 Lambda 函数
    lambda_env = {
        "Variables": {
            "LOCAL_DEV": "true",
            "DATABASE_URL": "postgresql://dev:dev@host.docker.internal:5433/photo_pipeline",
            "AWS_ENDPOINT_URL": "http://host.docker.internal:4566",
            "AWS_ACCESS_KEY_ID": "test",
            "AWS_SECRET_ACCESS_KEY": "test",
            "AWS_DEFAULT_REGION": "us-east-1",
        }
    }

    new_lambdas = [
        ("get-photo-ids", "get-photo-ids:latest"),
        ("photo-tagger", "photo-tagger:latest"),
        ("photo-vlm", "photo-vlm:latest"),
        ("photo-mark-complete", "photo-mark-complete:latest"),
    ]

    for func_name, image_uri in new_lambdas:
        try:
            lam.create_function(
                FunctionName=func_name,
                PackageType="Image",
                Role="arn:aws:iam::000000000000:role/lambda-role",
                Code={"ImageUri": image_uri},
                Environment=lambda_env,
                Timeout=30,
            )
            print(f"✅ Lambda '{func_name}' deployed (Container Image)")
        except lam.exceptions.ResourceConflictException:
            lam.update_function_code(
                FunctionName=func_name,
                ImageUri=image_uri,
            )
            print(f"ℹ️  Lambda '{func_name}' updated")

    # 6. 创建 Step Functions 状态机
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
