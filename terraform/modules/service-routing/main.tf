################################################################################
# ALB Target Group
################################################################################

locals {
  # ALB target group names are limited to 32 characters.
  # Use full name if it fits, otherwise truncate and append a 6-char hash suffix
  # to avoid collisions. Other resources don't have this limit.
  full_name = "${var.team}-${var.name}"
  tg_name = (
    length(local.full_name) <= 32
    ? local.full_name
    : "${substr(local.full_name, 0, 25)}-${substr(sha256(local.full_name), 0, 6)}"
  )
}

resource "aws_lb_target_group" "this" {
  name                          = local.tg_name
  port                          = var.port
  protocol                      = "HTTP"
  vpc_id                        = var.vpc_id
  target_type                   = "ip"
  deregistration_delay          = var.deregistration_delay
  load_balancing_algorithm_type = "round_robin"

  health_check {
    path                = var.health_check_path
    protocol            = "HTTP"
    healthy_threshold   = 2
    unhealthy_threshold = 2
    timeout             = 5
    interval            = 10
    matcher             = var.health_check_matcher
  }

  tags = {
    Name = "${var.team}-${var.name}"
  }
}

################################################################################
# ALB Listener Rule (host header routing)
################################################################################

resource "aws_lb_listener_rule" "this" {
  listener_arn = var.https_listener_arn
  priority     = var.listener_rule_priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this.arn
  }

  condition {
    host_header {
      values = [var.host_header]
    }
  }
}

################################################################################
# Route53 DNS Record (alias to ALB)
################################################################################

resource "aws_route53_record" "this" {
  zone_id = var.route53_zone_id
  name    = var.host_header
  type    = "A"

  alias {
    name                   = var.alb_dns_name
    zone_id                = var.alb_zone_id
    evaluate_target_health = true
  }
}
