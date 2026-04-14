output "s3_bucket" {
  value = aws_s3_bucket.uploads.bucket
}

output "state_machine_arn" {
  value = local.sfn_arn
}

output "lambda_functions" {
  value = { for k, v in aws_lambda_function.services : k => v.arn }
}

output "ecs_cluster" {
  value = aws_ecs_cluster.pipeline.name
}
