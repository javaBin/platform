################################################################################
# CloudWatch Log Group
################################################################################

resource "aws_cloudwatch_log_group" "this" {
  name              = "/ecs/${var.team}/${var.name}"
  retention_in_days = 30
}

################################################################################
# ECS Task Definition
#
# The root filesystem is read-only (ECS.5) and the app runs as a non-root user
# (ECS.20), so the only writable path is the "tmp" task volume at /tmp. Fargate
# creates that volume as an empty directory owned by root with mode 0755, which
# a non-root user cannot write to. A short-lived init container (root, no
# network use, read-only root filesystem) sets it to 1777 like a normal /tmp,
# and the app container starts only after it has exited successfully.
################################################################################

locals {
  # Container names allow underscores; app names (NAME_RE in the generator)
  # do not, so this can never clash with var.name.
  tmp_init_name = "platform_tmp_init"

  log_configuration = {
    logDriver = "awslogs"
    options = {
      "awslogs-group"         = aws_cloudwatch_log_group.this.name
      "awslogs-region"        = var.region
      "awslogs-stream-prefix" = var.name
    }
  }

  tmp_mount = {
    sourceVolume  = "tmp"
    containerPath = "/tmp"
    readOnly      = false
  }
}

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
      name      = local.tmp_init_name
      image     = var.tmp_init_image
      essential = false
      # Root only to chmod the volume; it exits immediately. Security Hub
      # ECS.20 for this container is suppressed in platform monitoring.
      user                   = "0"
      readonlyRootFilesystem = true
      command                = ["chmod", "1777", "/tmp"]
      mountPoints            = [local.tmp_mount]
      logConfiguration       = local.log_configuration
    },
    {
      name      = var.name
      image     = var.image
      essential = true
      user      = tostring(var.container_user)

      readonlyRootFilesystem = var.readonly_root_filesystem

      dependsOn = [
        {
          containerName = local.tmp_init_name
          condition     = "SUCCESS"
        }
      ]

      mountPoints = [local.tmp_mount]

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

      logConfiguration = local.log_configuration
    }
  ])
}

################################################################################
# ECS Service
################################################################################

# The ecs-deploy workflow owns which image runs and how many tasks: each deploy
# registers a copy of the family's latest revision pinned to the image digest
# built from the commit, and sets the desired count from the
# javabin:desired-count tag below (0 while the team is budget-suspended, see
# budget.tf). Terraform sets both only at creation, with
# 0 tasks, so a new service does not start (and fail) before its first image
# is pushed. A task definition change here reaches the service on the next
# deploy, which follows tf-apply in the same pipeline run.
resource "aws_ecs_service" "this" {
  name            = "${var.team}-${var.name}"
  cluster         = var.cluster_id
  task_definition = aws_ecs_task_definition.this.arn
  desired_count   = 0
  launch_type     = "FARGATE"

  # `aws ecs execute-command` into the tasks (compute.exec). The task role
  # gets the ssmmessages permissions it needs from the generator.
  enable_execute_command = var.enable_execute_command

  # Time a new task gets before failing ALB health checks count against it
  # (the target group marks a target unhealthy after ~20s). Only valid with a
  # load balancer.
  health_check_grace_period_seconds = var.target_group_arn != "" ? var.health_check_grace_period_seconds : null

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

  # "0" while the team is budget-suspended (budget.tf), so neither this apply
  # nor the deploy after it scales the service back up.
  tags = {
    "javabin:desired-count" = local.budget_suspended ? "0" : tostring(var.desired_count)
  }

  lifecycle {
    ignore_changes = [task_definition, desired_count]
  }

  depends_on = [aws_ecs_task_definition.this]
}
