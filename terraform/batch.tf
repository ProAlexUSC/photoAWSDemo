# AWS Batch resources for GPU Worker (AWS-only; local 用 docker compose run)
# 仅在 environment == "aws" 时创建；本地路径通过 count = 0 完全绕过。

##############################################
# Default VPC / Subnets / Security Group
##############################################

data "aws_vpc" "default" {
  count   = local.is_local ? 0 : 1
  default = true
}

data "aws_subnets" "default" {
  count = local.is_local ? 0 : 1

  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default[0].id]
  }
}

data "aws_security_group" "default" {
  count  = local.is_local ? 0 : 1
  vpc_id = data.aws_vpc.default[0].id
  name   = "default"
}

##############################################
# IAM: Batch Service Role
##############################################

resource "aws_iam_role" "batch_service" {
  count = local.is_local ? 0 : 1

  name = "photo-pipeline-batch-service"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "batch.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "batch_service" {
  count = local.is_local ? 0 : 1

  role       = aws_iam_role.batch_service[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSBatchServiceRole"
}

##############################################
# IAM: EC2 Instance Role (Compute Environment EC2s)
##############################################

resource "aws_iam_role" "batch_ec2" {
  count = local.is_local ? 0 : 1

  name = "photo-pipeline-batch-ec2"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "batch_ec2" {
  count = local.is_local ? 0 : 1

  role       = aws_iam_role.batch_ec2[0].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEC2ContainerServiceforEC2Role"
}

resource "aws_iam_instance_profile" "batch_ec2" {
  count = local.is_local ? 0 : 1

  name = "photo-pipeline-batch-ec2"
  role = aws_iam_role.batch_ec2[0].name
}

##############################################
# IAM: Job Role (Worker 容器运行时权限)
##############################################

resource "aws_iam_role" "batch_job" {
  count = local.is_local ? 0 : 1

  name = "photo-pipeline-batch-job"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "ecs-tasks.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy" "batch_job" {
  count = local.is_local ? 0 : 1

  name = "photo-pipeline-batch-job-policy"
  role = aws_iam_role.batch_job[0].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:ListBucket"
        ]
        Resource = [
          aws_s3_bucket.uploads.arn,
          "${aws_s3_bucket.uploads.arn}/*"
        ]
      },
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents"
        ]
        Resource = "*"
      }
    ]
  })
}

##############################################
# Compute Environment — GPU EC2, scale-to-zero
##############################################

resource "aws_batch_compute_environment" "gpu" {
  count = local.is_local ? 0 : 1

  compute_environment_name = "photo-pipeline-gpu"
  type                     = "MANAGED"
  state                    = "ENABLED"
  service_role             = aws_iam_role.batch_service[0].arn

  compute_resources {
    type                = "EC2"
    allocation_strategy = "BEST_FIT_PROGRESSIVE"

    min_vcpus     = var.batch_min_vcpus
    max_vcpus     = var.batch_max_vcpus
    desired_vcpus = 0

    instance_type = var.batch_instance_types

    subnets            = data.aws_subnets.default[0].ids
    security_group_ids = [data.aws_security_group.default[0].id]

    instance_role = aws_iam_instance_profile.batch_ec2[0].arn
  }

  depends_on = [aws_iam_role_policy_attachment.batch_service]

  # AWS Batch 会自己根据 job 负载动态调节 desired_vcpus；手动 scale-down 不被允许。
  # tofu 一旦观测到漂移（例如 AWS 自动扩容把 desired_vcpus 从 0 改成 4），
  # 会报 "Manually scaling down compute environment is not supported"。
  # 这里告诉 tofu 忽略运行时的 desired_vcpus 漂移。
  lifecycle {
    ignore_changes = [compute_resources[0].desired_vcpus]
  }
}

##############################################
# Job Queue
##############################################

resource "aws_batch_job_queue" "gpu" {
  count = local.is_local ? 0 : 1

  name     = "photo-pipeline-gpu-queue"
  priority = 1
  state    = "ENABLED"

  compute_environment_order {
    order               = 1
    compute_environment = aws_batch_compute_environment.gpu[0].arn
  }
}

##############################################
# Job Definition — Worker (GPU)
##############################################

resource "aws_batch_job_definition" "worker" {
  count = local.is_local ? 0 : 1

  name = "photo-worker"
  type = "container"

  container_properties = jsonencode({
    image      = var.worker_image
    vcpus      = 4
    memory     = var.worker_memory
    jobRoleArn = aws_iam_role.batch_job[0].arn

    resourceRequirements = var.batch_use_gpu ? [
      { type = "GPU", value = "1" }
    ] : []

    environment = [
      { name = "DATABASE_URL", value = var.lambda_database_url },
      { name = "S3_BUCKET", value = var.s3_bucket_name },
      { name = "LANGFUSE_PUBLIC_KEY", value = var.langfuse_public_key },
      { name = "LANGFUSE_SECRET_KEY", value = var.langfuse_secret_key },
      { name = "LANGFUSE_HOST", value = var.langfuse_host }
      # BATCH_ID / S3_KEYS / LANGFUSE_TRACE_ID / LANGFUSE_PARENT_OBS_ID
      # 通过 pipeline-aws.json 的 ContainerOverrides 在运行时注入。
    ]
  })
}

##############################################
# SFN role -> Batch + EventBridge (SubmitJob.sync)
##############################################
# SFN `arn:aws:states:::batch:submitJob.sync` 需要 batch:* 和 events:*

resource "aws_iam_role_policy" "sfn_batch" {
  count = local.is_local ? 0 : 1

  name = "photo-pipeline-sfn-batch"
  role = aws_iam_role.sfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "batch:SubmitJob",
          "batch:DescribeJobs",
          "batch:TerminateJob"
        ]
        Resource = "*"
      },
      {
        Effect = "Allow"
        Action = [
          "events:PutTargets",
          "events:PutRule",
          "events:DescribeRule"
        ]
        Resource = "*"
      },
      {
        Effect   = "Allow"
        Action   = ["lambda:InvokeFunction"]
        Resource = "*"
      }
    ]
  })
}
