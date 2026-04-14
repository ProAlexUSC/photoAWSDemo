resource "aws_ecs_cluster" "pipeline" {
  name = "photo-pipeline"
}

resource "aws_ecs_task_definition" "worker" {
  family = "photo-worker"

  container_definitions = jsonencode([{
    name      = "worker"
    image     = var.worker_image
    essential = true
    memory    = var.worker_memory
  }])
}
