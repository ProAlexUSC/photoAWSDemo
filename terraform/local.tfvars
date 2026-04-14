environment = "local"
aws_region  = "us-east-1"

# MiniStack endpoint（从主机访问）
aws_endpoint_url = "http://localhost:4566"
aws_access_key   = "test"
aws_secret_key   = "test"

# DB 连接（从主机视角，用于 Terraform provider）
database_url = "postgresql://dev:dev@localhost:5433/photo_pipeline"

# Lambda 内部网络（MiniStack Warm Workers 在 compose 网络内）
lambda_database_url     = "postgresql://dev:dev@postgres:5432/photo_pipeline"
lambda_aws_endpoint_url = "http://ministack:4566"

# 本地用 Zip 部署
lambda_runtime = "python3.12"

# Worker
worker_image  = "photo-worker:latest"
worker_memory = 2048

# 状态机
sfn_definition_file = "pipeline-local.json"
