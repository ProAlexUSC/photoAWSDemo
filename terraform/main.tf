terraform {
  required_version = ">= 1.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = var.aws_region

  # 仅本地 MiniStack 用硬编码 key/secret；真 AWS 留空由 SDK 走 credential chain（env / ~/.aws/credentials / IAM role）
  access_key = var.aws_endpoint_url != null ? var.aws_access_key : null
  secret_key = var.aws_endpoint_url != null ? var.aws_secret_key : null

  dynamic "endpoints" {
    for_each = var.aws_endpoint_url != null ? [1] : []
    content {
      s3             = var.aws_endpoint_url
      sqs            = var.aws_endpoint_url
      lambda         = var.aws_endpoint_url
      ecs            = var.aws_endpoint_url
      iam            = var.aws_endpoint_url
      stepfunctions  = var.aws_endpoint_url
    }
  }

  # MiniStack doesn't validate credentials
  skip_credentials_validation = var.environment == "local"
  skip_metadata_api_check     = var.environment == "local"
  skip_requesting_account_id  = var.environment == "local"

  default_tags {
    tags = {
      Project     = "photo-pipeline"
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

locals {
  is_local = var.environment == "local"

  lambda_env = merge(
    {
      LOCAL_DEV            = local.is_local ? "true" : "false"
      DATABASE_URL         = var.lambda_database_url
      STATE_MACHINE_ARN    = local.sfn_arn
      LANGFUSE_PUBLIC_KEY  = var.langfuse_public_key
      LANGFUSE_SECRET_KEY  = var.langfuse_secret_key
      LANGFUSE_HOST        = var.langfuse_host
    },
    local.is_local ? {
      AWS_ENDPOINT_URL      = var.lambda_aws_endpoint_url
      AWS_ACCESS_KEY_ID     = var.aws_access_key
      AWS_SECRET_ACCESS_KEY = var.aws_secret_key
      AWS_DEFAULT_REGION    = var.aws_region
    } : {}
  )

  lambdas = {
    "photo-scheduler" = {
      service = "scheduler"
      handler = "scheduler.handler.handler"
    }
    "get-photo-ids" = {
      service = "get_photo_ids"
      handler = "get_photo_ids.handler.handler"
    }
    "photo-tagger" = {
      service = "tagger"
      handler = "tagger.handler.handler"
    }
    "photo-vlm" = {
      service = "vlm_extractor"
      handler = "vlm_extractor.handler.handler"
    }
    "photo-mark-complete" = {
      service = "mark_complete"
      handler = "mark_complete.handler.handler"
    }
  }
}
