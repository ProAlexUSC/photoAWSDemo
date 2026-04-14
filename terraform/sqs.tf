resource "aws_sqs_queue" "dlq" {
  name = "scheduler-dlq"
}
