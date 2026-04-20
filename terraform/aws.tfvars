environment = "aws"
aws_region  = "us-west-1"

# 真 AWS — 不设 endpoint override
aws_endpoint_url = null

# Supabase (Transaction mode) —— 用 DB password 替换 REPLACE_ME 再 apply
lambda_database_url     = "postgresql://postgres.YOUR_PROJECT_REF:YOUR_DB_PASSWORD@aws-1-us-west-1.pooler.supabase.com:6543/postgres?sslmode=require"
lambda_aws_endpoint_url = null

# AWS Lambda Container Image（后续实现）
# lambda_runtime = null

# 暂时用 Zip
lambda_runtime = "python3.12"

# Worker — ECR image（已在 us-west-1 账号 961227453053 下）
worker_image  = "961227453053.dkr.ecr.us-west-1.amazonaws.com/photo-worker:latest"
worker_memory = 4096

# 状态机
sfn_definition_file = "pipeline-aws.json"

# S3 bucket 名（全球唯一，加 account+region 避免冲突）
s3_bucket_name = "photo-uploads-961227453053-us-west-1"

# AWS Batch（GPU EC2，空闲缩 0 省钱）
batch_min_vcpus      = 0
batch_max_vcpus      = 16
batch_instance_types = ["g4dn.xlarge"]
