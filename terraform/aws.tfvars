environment = "aws"
aws_region  = "us-east-1"

# 真 AWS — 不设 endpoint override
aws_endpoint_url = null

# Supabase (Transaction mode)
lambda_database_url     = "postgresql://postgres.xxx:password@aws-0-us-east-1.pooler.supabase.com:6543/postgres?sslmode=require"
lambda_aws_endpoint_url = null

# AWS Lambda Container Image（后续实现）
# lambda_runtime = null

# 暂时用 Zip
lambda_runtime = "python3.12"

# Worker — ECR image
worker_image  = "123456789012.dkr.ecr.us-east-1.amazonaws.com/photo-worker:latest"
worker_memory = 4096

# 状态机
sfn_definition_file = "pipeline-aws.json"
