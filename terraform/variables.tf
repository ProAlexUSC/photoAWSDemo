variable "environment" {
  description = "Deployment environment: local or aws"
  type        = string
  default     = "local"
}

variable "aws_region" {
  type    = string
  default = "us-east-1"
}

variable "aws_endpoint_url" {
  description = "Override AWS endpoint (MiniStack for local)"
  type        = string
  default     = null
}

variable "aws_access_key" {
  type    = string
  default = "test"
}

variable "aws_secret_key" {
  type    = string
  default = "test"
}

variable "lambda_database_url" {
  description = "PostgreSQL connection string from Lambda/container network"
  type        = string
}

variable "lambda_aws_endpoint_url" {
  description = "AWS endpoint from Lambda/container network"
  type        = string
  default     = null
}

variable "s3_bucket_name" {
  type    = string
  default = "photo-uploads"
}

variable "lambda_runtime" {
  description = "python3.12 for Zip, null for Container Image"
  type        = string
  default     = "python3.12"
}

variable "lambda_timeout" {
  type    = number
  default = 30
}

variable "worker_image" {
  description = "Docker image for worker ECS task"
  type        = string
  default     = "photo-worker:latest"
}

variable "worker_memory" {
  type    = number
  default = 2048
}

variable "langsmith_api_key" {
  description = "LangSmith API key (optional)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "langsmith_project" {
  type    = string
  default = "photo-pipeline"
}

variable "sfn_definition_file" {
  description = "Path to Step Functions state machine JSON"
  type        = string
  default     = "pipeline-local.json"
}
