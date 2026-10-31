################################################################################
# CloudWatch Log Group
################################################################################

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.team}/${var.name}"
  retention_in_days = 30
}

################################################################################
# ECS Task Definition
################################################################################

resource "aws_ecs_task_definition" "this" {
  family                   = "${var.team}-${var.name}"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.cpu
  memory                   = var.memory
  execution_role_arn       = var.execution_role_arn
  task_role_arn            = var.task_role_arn

  volume {
    name = "tmp"
  }

  container_definitions = jsonencode([
    {
      name      = var.name
      image     = var.image
      essential = true
      user      = tostring(var.container_user)

      readonlyRootFilesystem = var.readonly_root_filesystem

      mountPoints = [
        {
          sourceVolume  = "tmp"
          containerPath = "/tmp"
          readOnly      = false
        }
      ]

      portMappings = [
        {
          containerPort = var.port
          protocol      = "tcp"
        }
      ]

      environment = [
        for k, v in var.environment : {
          name  = k
          value = v
        }
      ]

      secrets = [
        for k, v in var.secrets : {
          name      = k
          valueFrom = v
        }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          "awslogs-group"         = aws_cloudwatch_log_group.this.name
          "awslogs-region"        = var.region
          "awslogs-stream-prefix" = var.name
        }
      }
    }
  ])
}

################################################################################
# ECS Service
################################################################################

# The ecs-deploy workflow owns which image runs and how many tasks: each deploy
# registers a copy of the family's latest revision pinned to the image digest
# built from the commit, and sets the desired count from the
# javabin:desired-count tag below. Terraform sets both only at creation, with
# 0 tasks, so a new service does not start (and fail) before its first image
# is pushed. A task definition change here reaches the service on the next
# deploy, which follows tf-apply in the same pipeline run.
resource "aws_ecs_service" "this" {
  name            = "${var.team}-${var.name}"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = 0
  launch_type     = "FARGATE"

  network_configuration {
    subnets          = var.subnet_ids
    security_groups  = var.security_group_ids
    assign_public_ip = false
  }

  dynamic "load_balancer" {
    for_each = var.target_group_arn != "" ? [1] : []
    content {
      target_group_arn = var.target_group_arn
      container_name   = var.name
      container_port   = var.port
    }
  }

  # Cloud Map registration (app-network), so peers reach the tasks privately
  dynamic "service_registries" {
    for_each = var.service_registry_arns
    content {
      registry_arn = service_registries.value
    }
  }

  # Roll back a deployment whose tasks never become healthy, instead of
  # leaving ecs-deploy waiting for services-stable to time out.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  enable_ecs_managed_tags = true
  propagate_tags          = "SERVICE"

  tags = {
    "javabin:desired-count" = tostring(var.desired_count)
  }

  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }

  depends_on = [aws_ecs_task_definition.this]
}
