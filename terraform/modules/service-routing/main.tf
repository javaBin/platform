################################################################################
# ALB Target Group
################################################################################

resource "aws_lb_target_group" "this" {
  name        = "${var.project}-${var.name}"
  port        = var.port
  protocol    = "HTTP"
  vpc_id      = var.vpc_id
  target_type = "ip"

  health_check {
    path                = var.health_check_path
    protocol            = "HTTP"
    healthy_threshold   = 2
    unhealthy_threshold = 3
    timeout             = 5
    interval            = 30
    matcher             = var.health_check_matcher
  }

  tags = {
    Name = "${var.project}-${var.name}"
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
