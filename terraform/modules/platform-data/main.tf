################################################################################
# Shared Platform Infrastructure — read-only data sources
#
# App repos call this module to discover shared infra by naming convention.
# No resources are created — this is purely data lookups.
################################################################################

data "aws_vpc" "main" {
  filter {
    name   = "tag:Name"
    values = ["${var.project}-vpc"]
  }
}

data "aws_subnets" "private" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.main.id]
  }
  filter {
    name   = "tag:tier"
    values = ["private"]
  }
}

data "aws_subnets" "public" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.main.id]
  }
  filter {
    name   = "tag:tier"
    values = ["public"]
  }
}

data "aws_security_group" "ecs_tasks" {
  filter {
    name   = "group-name"
    values = ["${var.project}-ecs-tasks-sg"]
  }
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.main.id]
  }
}

data "aws_lb" "main" {
  name = "${var.project}-platform-alb"
}

data "aws_lb_listener" "https" {
  load_balancer_arn = data.aws_lb.main.arn
  port              = 443
}

data "aws_ecs_cluster" "main" {
  cluster_name = "${var.project}-platform"
}

data "aws_iam_role" "ecs_execution" {
  name = "${var.project}-ecs-execution"
}

data "aws_route53_zone" "main" {
  name         = var.domain
  private_zone = false
}


# Note: the developer boundary ARN is constructed directly by expand-modules.py
# instead of using a data source, to avoid needing iam:GetPolicy permission.
