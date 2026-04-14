resource "aws_iam_role" "sfn" {
  name = "photo-pipeline-sfn"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "states.amazonaws.com" }
    }]
  })
}

# AWS: native resource (MiniStack 不支持 ListStateMachineVersions)
resource "aws_sfn_state_machine" "pipeline" {
  count = local.is_local ? 0 : 1

  name     = "photo-pipeline"
  role_arn = aws_iam_role.sfn.arn

  definition = file("${path.module}/../state-machines/${var.sfn_definition_file}")
}

# Local: 用脚本创建（绕过 MiniStack API 兼容性问题）
resource "terraform_data" "sfn_local" {
  count = local.is_local ? 1 : 0

  input = filemd5("${path.module}/../state-machines/${var.sfn_definition_file}")

  provisioner "local-exec" {
    command     = "${path.module}/create_sfn.sh ${abspath("${path.module}/../state-machines/${var.sfn_definition_file}")}"
    environment = {
      AWS_ENDPOINT_URL      = var.aws_endpoint_url
      AWS_ACCESS_KEY_ID     = var.aws_access_key
      AWS_SECRET_ACCESS_KEY = var.aws_secret_key
      AWS_DEFAULT_REGION    = var.aws_region
    }
  }
}

locals {
  sfn_arn = local.is_local ? "arn:aws:states:${var.aws_region}:000000000000:stateMachine:photo-pipeline" : (length(aws_sfn_state_machine.pipeline) > 0 ? aws_sfn_state_machine.pipeline[0].arn : "")
}
