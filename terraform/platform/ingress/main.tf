################################################################################
# Existing javazone.no hosted zone (DO NOT create — already exists)
################################################################################

data "aws_route53_zone" "main" {
  name         = var.domain
  private_zone = false
}

################################################################################
# Wildcard ACM Certificate for *.javazone.no
################################################################################

resource "aws_acm_certificate" "wildcard" {
  domain_name       = "*.${var.domain}"
  validation_method = "DNS"

  tags = {
    Name = "${var.domain}-wildcard"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_acm_certificate_validation" "wildcard" {
  certificate_arn         = aws_acm_certificate.wildcard.arn
  validation_record_fqdns = [for record in aws_route53_record.acm_validation : record.fqdn]
}

################################################################################
# ACM DNS Validation Records
################################################################################

resource "aws_route53_record" "acm_validation" {
  for_each = {
    for dvo in aws_acm_certificate.wildcard.domain_validation_options : dvo.domain_name => {
      name   = dvo.resource_record_name
      record = dvo.resource_record_value
      type   = dvo.resource_record_type
    }
  }

  zone_id = data.aws_route53_zone.main.zone_id
  name    = each.value.name
  type    = each.value.type
  ttl     = 60
  records = [each.value.record]

  allow_overwrite = true
}

################################################################################
# Wildcard certs for the other app domains (*.java.no, *.javabin.no), served
# by SNI alongside the default *.javazone.no cert
################################################################################

resource "aws_acm_certificate" "extra" {
  for_each          = var.extra_domains
  domain_name       = "*.${each.key}"
  validation_method = "DNS"

  tags = {
    Name = "${each.key}-wildcard"
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_route53_record" "extra_acm_validation" {
  for_each = {
    for d, cert in aws_acm_certificate.extra : d => one(cert.domain_validation_options)
  }

  zone_id         = var.extra_domains[each.key]
  name            = each.value.resource_record_name
  type            = each.value.resource_record_type
  ttl             = 60
  records         = [each.value.resource_record_value]
  allow_overwrite = true
}

resource "aws_acm_certificate_validation" "extra" {
  for_each                = aws_acm_certificate.extra
  certificate_arn         = each.value.arn
  validation_record_fqdns = [aws_route53_record.extra_acm_validation[each.key].fqdn]
}

resource "aws_lb_listener_certificate" "extra" {
  for_each        = aws_acm_certificate_validation.extra
  listener_arn    = aws_lb_listener.https.arn
  certificate_arn = each.value.certificate_arn
}

################################################################################
# Application Load Balancer
################################################################################

resource "aws_lb" "main" {
  name                       = "${var.project}-platform-alb"
  internal                   = false
  load_balancer_type         = "application"
  security_groups            = [var.alb_sg_id]
  subnets                    = var.public_subnet_ids
  enable_deletion_protection = true
  # ELB.4: drop headers with invalid field names (request smuggling hygiene)
  drop_invalid_header_fields = true

  tags = {
    Name = "${var.project}-platform-alb"
    team = "shared"
  }
}

################################################################################
# HTTPS Listener (443) — default 404 until services are registered
################################################################################

resource "aws_lb_listener" "https" {
  load_balancer_arn = aws_lb.main.arn
  port              = 443
  protocol          = "HTTPS"
  ssl_policy        = "ELBSecurityPolicy-TLS13-1-2-2021-06"
  certificate_arn   = aws_acm_certificate_validation.wildcard.certificate_arn

  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      message_body = "Not Found"
      status_code  = "404"
    }
  }

  tags = {
    team = "shared"
  }
}

################################################################################
# HTTP Listener (80) — redirect to HTTPS
################################################################################

resource "aws_lb_listener" "http_redirect" {
  load_balancer_arn = aws_lb.main.arn
  port              = 80
  protocol          = "HTTP"

  default_action {
    type = "redirect"

    redirect {
      port        = "443"
      protocol    = "HTTPS"
      status_code = "HTTP_301"
    }
  }
}
