data "external" "lambda_zip" {
  for_each = local.lambdas

  # 本地 MiniStack (Apple Silicon ARM) 的 Warm Worker 预装了 psycopg2-binary/boto3/langfuse；
  # 不要把 x86 wheel 打进 zip 污染 sys.path，否则 Lambda 起不来。
  # AWS Lambda runtime 无这些依赖，必须打进 zip（即 target=aws）。
  program = ["bash", "${path.module}/build_lambda_zip.sh", each.value.service, local.is_local ? "local" : "aws"]
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

# Scheduler Lambda 需要调 SFN start_execution；其他 Lambda 不需要但共用 role，许可宽一点
resource "aws_iam_role_policy" "lambda_sfn" {
  count = local.is_local ? 0 : 1
  name  = "${aws_iam_role.lambda.name}-sfn"
  role  = aws_iam_role.lambda.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["states:StartExecution", "states:DescribeExecution"]
      Resource = "arn:aws:states:*:*:stateMachine:photo-pipeline"
    }]
  })
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
