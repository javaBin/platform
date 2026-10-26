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

  # Fail closed: on a new app nothing is served until the sign-in rules
  # (evaluated first, lower priority numbers) exist.
  depends_on = [aws_lb_listener_rule.login, aws_lb_listener_rule.public]
}

################################################################################
# Sign-in at the load balancer (app.yaml routing.login)
#
# Each protected rule gets its own Cognito client, requested through the
# cognito-app-client module (slot login-N) and created by the platform's
# app-auth-sync Lambda. Group restrictions are enforced by Cognito: the
# auth-claims trigger refuses tokens for the client when the user is in none
# of its allowed groups, so the ALB never gets a session for them. Cognito's
# hosted-UI session means a user signs in once for all rules.
#
# authenticate-oidc (not authenticate-cognito) because the latter requires
# cognito-idp:DescribeUserPoolClient on the platform's pools, which the
# developer boundary denies — it would expose every team's client secrets.
################################################################################

locals {
  login_protected = { for k, v in var.login_rules : k => v if !v.public }
  login_public    = { for k, v in var.login_rules : k => v if v.public }

  # The ALB accepts any valid session cookie of the rule's name. Naming the
  # cookie after the rule's slot, pool and groups means a change to any of
  # them (tightened groups, a stricter rule taking over a slot after a
  # reorder) invalidates existing sessions and forces a fresh sign-in.
  login_cookie = {
    for k, v in local.login_protected :
    k => "javabin-auth-${k}-${substr(sha256(jsonencode([k, v.pool, sort(v.groups)])), 0, 10)}"
  }
}

module "login_client" {
  source   = "../cognito-app-client"
  for_each = local.login_protected

  app_name       = var.name
  team           = var.team
  project        = var.project
  slot           = "login-${trimprefix(each.key, "p")}"
  pool           = each.value.pool
  client_type    = "confidential"
  callback_urls  = ["https://${var.host_header}/oauth2/idpresponse"]
  logout_urls    = ["https://${var.host_header}/"]
  allowed_groups = each.value.groups
}

# The client is created asynchronously (SSM -> EventBridge -> Lambda). Wait
# before reading its credentials; re-armed only when the request changes. If
# the Lambda is slower, the read fails with ParameterNotFound — re-run.
resource "time_sleep" "login_client" {
  for_each = local.login_protected

  create_duration = var.login_client_wait

  triggers = {
    request = module.login_client[each.key].request_value
  }
}

data "aws_ssm_parameter" "login_client_id" {
  for_each = local.login_protected

  name       = module.login_client[each.key].client_id_parameter_name
  depends_on = [time_sleep.login_client]
}

data "aws_ssm_parameter" "login_client_secret" {
  for_each = local.login_protected

  name            = module.login_client[each.key].client_secret_parameter_name
  with_decryption = true
  depends_on      = [time_sleep.login_client]
}

resource "aws_lb_listener_rule" "login" {
  for_each = local.login_protected

  listener_arn = var.https_listener_arn
  priority     = each.value.priority

  action {
    type  = "authenticate-oidc"
    order = 1

    authenticate_oidc {
      issuer                     = module.login_client[each.key].issuer_url
      authorization_endpoint     = "https://${module.login_client[each.key].user_pool_domain}/oauth2/authorize"
      token_endpoint             = "https://${module.login_client[each.key].user_pool_domain}/oauth2/token"
      user_info_endpoint         = "https://${module.login_client[each.key].user_pool_domain}/oauth2/userInfo"
      client_id                  = nonsensitive(data.aws_ssm_parameter.login_client_id[each.key].value)
      client_secret              = data.aws_ssm_parameter.login_client_secret[each.key].value
      scope                      = "openid email profile"
      session_cookie_name        = local.login_cookie[each.key]
      session_timeout            = var.login_session_timeout
      on_unauthenticated_request = "authenticate"
      # No identity_provider parameter: app-auth-sync gives every client the
      # pool's sign-in IdP list, and with a single IdP the hosted UI goes
      # straight to it. A hardcoded IdP would break at the IdP cutover.
    }
  }

  action {
    type             = "forward"
    order            = 2
    target_group_arn = aws_lb_target_group.this.arn
  }

  condition {
    host_header {
      values = [var.host_header]
    }
  }

  condition {
    path_pattern {
      values = each.value.paths
    }
  }

  tags = {
    Name = "${var.team}-${var.name}-${each.key}"
  }
  lifecycle {
    precondition {
      condition     = each.value.priority < var.listener_rule_priority
      error_message = "A login rule's priority must be below listener_rule_priority, so it is evaluated before the host rule."
    }
  }
}

# public: true — opt-out paths, forwarded without sign-in
resource "aws_lb_listener_rule" "public" {
  for_each = local.login_public

  listener_arn = var.https_listener_arn
  priority     = each.value.priority

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.this.arn
  }

  condition {
    host_header {
      values = [var.host_header]
    }
  }

  condition {
    path_pattern {
      values = each.value.paths
    }
  }

  tags = {
    Name = "${var.team}-${var.name}-${each.key}"
  }
  lifecycle {
    precondition {
      condition     = each.value.priority < var.listener_rule_priority
      error_message = "A login rule's priority must be below listener_rule_priority, so it is evaluated before the host rule."
    }
  }
}

################################################################################
# Route53 DNS Record (alias to ALB)
################################################################################

# allow_overwrite (routing.adopt_dns in app.yaml) lets Terraform take over a
# record that already exists, e.g. a hand-made record for an app that moves
# onto the platform: Route53 UPSERTs it instead of failing on CREATE. From
# then on the record is managed here, and destroying it deletes it.
resource "aws_route53_record" "this" {
  zone_id         = var.route53_zone_id
  name            = var.host_header
  type            = "A"
  allow_overwrite = var.allow_dns_overwrite ? true : null # null = unchanged for existing apps

  alias {
    name                   = var.alb_dns_name
    zone_id                = var.alb_zone_id
    evaluate_target_health = true
  }
}
