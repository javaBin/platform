################################################################################
# App Network — the app's own security group, private DNS name and its
# app-to-app request (app.yaml `calls:` / `provides:`).
#
# Emitted for every app. Resources here are owned by the app's team:
#
#   - Security group {team}-{name}-tasks for the app's ECS tasks. The ALB may
#     reach the app port (when routed); nothing else may, until a peer rule
#     is added. No inline rules, so Terraform never strips peer rules.
#   - Cloud Map service {team}-{name} in the shared private namespace
#     {project}.internal, so peers reach http://{team}-{name}.{project}.internal:{port}
#     from inside the VPC.
#   - SSM parameter /{project}/apps/{team}/{name}/network with the app's SG,
#     port, calls and provides. Only this team can write under its path
#     (developer boundary DenyBadName), so the path is the app's identity.
#
# The platform app-peers-sync Lambda reads every app's `network` parameter
# and, for each edge both sides declared (caller `calls` the callee and the
# callee lists the caller in `consumers`):
#
#   - adds an ingress rule on the callee's SG from the caller's SG, on the
#     callee's port;
#   - for scopes, keeps a Cognito resource server {team}-{name} for the callee
#     and a client-credentials client m2m.{team}-{name} for the caller in the
#     internal pool;
#   - writes into the caller's own path:
#       peer-{t}-{a}-url    (String)       http://{t}-{a}.{project}.internal:{port}
#       peer-{t}-{a}-scopes (String)       granted scopes ("none" if none), calls with scopes
#       m2m-client-id       (String)
#       m2m-client-secret   (SecureString)
#       peers-status        (String JSON)  active / pending-consent / callee-missing / callee-invalid
#
# Those parameter ARNs are deterministic, so the task definition references
# them up front (outputs below); deriving them from the request parameter's
# ARN makes the task definition wait for the request.
################################################################################

data "aws_region" "current" {}

data "aws_security_group" "alb" {
  count = var.alb_ingress ? 1 : 0

  filter {
    name   = "group-name"
    values = ["${var.project}-alb-sg"]
  }
  filter {
    name   = "vpc-id"
    values = [var.vpc_id]
  }
}

data "aws_service_discovery_dns_namespace" "internal" {
  name = "${var.project}.internal"
  type = "DNS_PRIVATE"
}

data "aws_cognito_user_pools" "internal" {
  count = local.uses_scopes ? 1 : 0
  name  = var.internal_pool_name
}

locals {
  region      = data.aws_region.current.name
  uses_scopes = length(var.provides_scopes) > 0 || anytrue([for s in values(var.calls) : length(s) > 0])
  pool_id     = local.uses_scopes ? tolist(data.aws_cognito_user_pools.internal[0].ids)[0] : ""
  issuer_url  = local.uses_scopes ? "https://cognito-idp.${local.region}.amazonaws.com/${local.pool_id}" : ""

  # The `network` request without the security group ID
  request = {
    version = 1
    port    = var.port
    calls   = var.calls
    provides = {
      scopes    = var.provides_scopes
      consumers = var.consumers
    }
  }

  # /<project>/apps/<team>/<name>, as an ARN prefix
  param_arn_prefix = trimsuffix(aws_ssm_parameter.network.arn, "/network")
}

resource "aws_security_group" "tasks" {
  name                   = "${var.team}-${var.name}-tasks"
  description            = "ECS tasks of ${var.team}-${var.name}; peer rules are managed by ${var.project}-app-peers-sync"
  vpc_id                 = var.vpc_id
  revoke_rules_on_delete = true

  tags = {
    Name = "${var.team}-${var.name}-tasks"
    # Lets app-peers-sync add peer rules to this group (its IAM condition)
    "javabin-network" = "app"
  }
}

resource "aws_vpc_security_group_ingress_rule" "alb" {
  count = var.alb_ingress ? 1 : 0

  security_group_id            = aws_security_group.tasks.id
  referenced_security_group_id = data.aws_security_group.alb[0].id
  from_port                    = var.port
  to_port                      = var.port
  ip_protocol                  = "tcp"
  description                  = "ALB to the app port"
}

resource "aws_vpc_security_group_egress_rule" "all" {
  security_group_id = aws_security_group.tasks.id
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "-1"
  description       = "All outbound"
}

resource "aws_service_discovery_service" "this" {
  name = "${var.team}-${var.name}"

  dns_config {
    namespace_id   = data.aws_service_discovery_dns_namespace.internal.id
    routing_policy = "MULTIVALUE"

    dns_records {
      ttl  = 10
      type = "A"
    }
  }

  # ECS reports task health, so only healthy tasks are returned
  health_check_custom_config {
    failure_threshold = 1
  }

  # Deregister leftover instances instead of failing a destroy
  force_destroy = true
}

resource "aws_ssm_parameter" "network" {
  name        = "/${var.project}/apps/${var.team}/${var.name}/network"
  description = "App-to-app request for ${var.team}-${var.name} — read by the platform app-peers-sync Lambda"
  type        = "String"
  value       = jsonencode(merge(local.request, { security_group_id = aws_security_group.tasks.id }))

  lifecycle {
    # Standard tier: values up to 4 KB. Checked with the longest security
    # group ID; generate-modules.py checks the same with a clearer message.
    precondition {
      condition     = length(jsonencode(merge(local.request, { security_group_id = "sg-00000000000000000" }))) <= 4096
      error_message = "The app-to-app request (calls / provides) is over 4096 bytes; shorten provides.scopes descriptions or list fewer scopes, calls or consumers."
    }
  }
}
