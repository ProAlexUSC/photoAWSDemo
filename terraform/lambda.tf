data "external" "lambda_zip" {
  for_each = local.lambdas

  program = ["bash", "${path.module}/build_lambda_zip.sh", each.value.service]
}

resource "aws_iam_role" "lambda" {
  name = "photo-pipeline-lambda"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Action    = "sts:AssumeRole"
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
    }]
  })
}

resource "aws_iam_role_policy_attachment" "lambda_basic" {
  count      = local.is_local ? 0 : 1
  role       = aws_iam_role.lambda.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "services" {
  for_each = local.lambdas

  function_name = each.key
  role          = aws_iam_role.lambda.arn
  runtime       = var.lambda_runtime
  handler       = each.value.handler
  timeout       = var.lambda_timeout

  filename         = data.external.lambda_zip[each.key].result.path
  source_code_hash = data.external.lambda_zip[each.key].result.hash

  environment {
    variables = local.lambda_env
  }

  tracing_config {
    mode = local.is_local ? "PassThrough" : "Active"
  }
}
