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

variable "worker_vcpus" {
  description = "Batch Worker 容器 vCPU 数；free-tier 账号最大 2"
  type        = number
  default     = 4
}

variable "langfuse_public_key" {
  description = "Langfuse public key (pk-lf-...); 留空则 Lambda 侧 Langfuse no-op"
  type        = string
  default     = ""
  sensitive   = true
}

variable "langfuse_secret_key" {
  description = "Langfuse secret key (sk-lf-...)"
  type        = string
  default     = ""
  sensitive   = true
}

variable "langfuse_host" {
  description = "Langfuse host, e.g. https://us.cloud.langfuse.com"
  type        = string
  default     = "https://us.cloud.langfuse.com"
}

variable "sfn_definition_file" {
  description = "Path to Step Functions state machine JSON"
  type        = string
  default     = "pipeline-local.json"
}

##############################################
# AWS Batch (仅 AWS 路径使用)
##############################################

variable "batch_min_vcpus" {
  description = "Batch CE 最小 vCPU；设 0 空闲时缩到 0 省钱"
  type        = number
  default     = 0
}

variable "batch_max_vcpus" {
  description = "Batch CE 最大 vCPU"
  type        = number
  default     = 16
}

variable "batch_instance_types" {
  description = "Batch CE 可用机型（GPU 默认 g4dn.xlarge；CPU 时换 c5.xlarge）"
  type        = list(string)
  default     = ["g4dn.xlarge"]
}

variable "batch_use_gpu" {
  description = "Batch Worker 是否申请 GPU 资源（false 时走纯 CPU，绕 GPU 配额）"
  type        = bool
  default     = true
}
