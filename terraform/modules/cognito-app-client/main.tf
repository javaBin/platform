################################################################################
# Cognito App Client — requests an OAuth client in a platform user pool.
#
# App CI cannot create the client itself: the pools belong to the platform
# (team=platform), so the developer boundary denies cognito-idp writes, and a
# pool-level grant would let any team rewrite every other team's client.
#
# Instead this module writes the desired client config to an SSM parameter in
# the app's own path, /<project>/apps/<team>/<app>/auth. The boundary only lets
# a team write under its own prefix, so the path is proof of ownership. The
# platform's app-auth-sync Lambda reacts to the parameter (EventBridge) and
# creates/updates/deletes the client named <team>-<app>, then writes:
#
#   /<project>/apps/<team>/<app>/cognito-client-id                 (String)
#   /<project>/apps/<team>/<app>/cognito-client-secret             (SecureString)
#   /<project>/apps/<team>/<app>/cognito-client-identity-provider  (String)
#   /<project>/apps/<team>/<app>/auth-status                       (String JSON)
#
# Those parameters are owned by the Lambda, not by this Terraform. Their ARNs
# are deterministic, so the ECS task definition can reference them up front;
# the shared execution role reads /<project>/apps/* at task launch.
################################################################################

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

data "aws_cognito_user_pools" "this" {
  name = var.pool == "internal" ? var.internal_pool_name : var.external_pool_name
}

locals {
  region     = data.aws_region.current.name
  account_id = data.aws_caller_identity.current.account_id
  pool_id    = tolist(data.aws_cognito_user_pools.this.ids)[0]

  param_prefix = "/${var.project}/apps/${var.team}/${var.app_name}"
  param_arn    = "arn:aws:ssm:${local.region}:${local.account_id}:parameter${local.param_prefix}"

  # Internal pool has a Cognito-managed domain (deterministic FQDN). The
  # external pool's custom domain only exists when the platform has a
  # certificate wired up, so the caller passes it in.
  pool_domain = var.pool == "internal" ? "${var.internal_pool_name}.auth.${local.region}.amazoncognito.com" : var.external_pool_custom_domain
}

# Validate-only apps (client_type = "none") only verify tokens issued to
# other clients; they get pool metadata but request no client.
resource "aws_ssm_parameter" "auth_request" {
  count = var.client_type == "none" ? 0 : 1

  name        = "${local.param_prefix}/auth"
  description = "Cognito client request for ${var.team}-${var.app_name} — read by the platform app-auth-sync Lambda"
  type        = "String"
  value = jsonencode({
    pool           = var.pool
    client_type    = var.client_type
    callback_urls  = var.callback_urls
    logout_urls    = var.logout_urls
    scopes         = var.allowed_oauth_scopes
    allowed_groups = var.allowed_groups
  })
}
