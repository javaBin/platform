################################################################################
# Cognito App Client — registers an app with internal/external user pools.
#
# The module looks up its target pool by NAME (not ID), so callers don't need
# to plumb pool IDs through every layer. Pool names are unique by convention
# in this account (one pool per role: javabin-internal, javabin-external).
#
# Creates an aws_cognito_user_pool_client and stores credentials in SSM so the
# app can read them at runtime without hardcoding.
################################################################################

data "aws_region" "current" {}
data "aws_caller_identity" "current" {}

data "aws_cognito_user_pools" "internal" {
  count = local.create_internal ? 1 : 0
  name  = var.internal_pool_name
}

data "aws_cognito_user_pools" "external" {
  count = local.create_external ? 1 : 0
  name  = var.external_pool_name
}

locals {
  create_internal = var.pool == "internal" || var.pool == "both"
  create_external = var.pool == "external" || var.pool == "both"

  region     = data.aws_region.current.region
  account_id = data.aws_caller_identity.current.account_id

  internal_pool_id = local.create_internal ? tolist(data.aws_cognito_user_pools.internal[0].ids)[0] : ""
  external_pool_id = local.create_external ? tolist(data.aws_cognito_user_pools.external[0].ids)[0] : ""

  internal_pool_arn = local.create_internal ? "arn:aws:cognito-idp:${local.region}:${local.account_id}:userpool/${local.internal_pool_id}" : ""
  external_pool_arn = local.create_external ? "arn:aws:cognito-idp:${local.region}:${local.account_id}:userpool/${local.external_pool_id}" : ""

  # Internal pool always has a Cognito-managed domain (deterministic FQDN).
  # External pool's domain is custom (auth-external.<domain>) and only exists
  # when a certificate is wired up at the platform level — caller passes it in.
  internal_pool_domain = local.create_internal ? "${var.internal_pool_name}.auth.${local.region}.amazoncognito.com" : ""
  external_pool_domain = local.create_external ? var.external_pool_custom_domain : ""

  internal_issuer_url = local.create_internal ? "https://cognito-idp.${local.region}.amazonaws.com/${local.internal_pool_id}" : ""
  external_issuer_url = local.create_external ? "https://cognito-idp.${local.region}.amazonaws.com/${local.external_pool_id}" : ""
}

################################################################################
# Internal Pool Client
################################################################################

resource "aws_cognito_user_pool_client" "internal" {
  count = local.create_internal ? 1 : 0

  name         = "${var.team}-${var.app_name}"
  user_pool_id = local.internal_pool_id

  generate_secret = var.generate_secret

  allowed_oauth_flows                  = var.allowed_oauth_flows
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = var.allowed_oauth_scopes
  supported_identity_providers         = ["COGNITO"]

  callback_urls = var.callback_urls
  logout_urls   = var.logout_urls

  explicit_auth_flows = [
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]
}

resource "aws_ssm_parameter" "internal_client_id" {
  count = local.create_internal ? 1 : 0

  name  = "/${var.project}/platform-apps/${var.app_name}/cognito-internal-client-id"
  type  = "String"
  value = aws_cognito_user_pool_client.internal[0].id
}

resource "aws_ssm_parameter" "internal_client_secret" {
  count = local.create_internal && var.generate_secret ? 1 : 0

  name  = "/${var.project}/platform-apps/${var.app_name}/cognito-internal-client-secret"
  type  = "SecureString"
  value = aws_cognito_user_pool_client.internal[0].client_secret
}

################################################################################
# External Pool Client
################################################################################

resource "aws_cognito_user_pool_client" "external" {
  count = local.create_external ? 1 : 0

  name         = "${var.team}-${var.app_name}"
  user_pool_id = local.external_pool_id

  generate_secret = var.generate_secret

  allowed_oauth_flows                  = var.allowed_oauth_flows
  allowed_oauth_flows_user_pool_client = true
  allowed_oauth_scopes                 = var.allowed_oauth_scopes
  supported_identity_providers         = ["COGNITO"]

  callback_urls = var.callback_urls
  logout_urls   = var.logout_urls

  explicit_auth_flows = [
    "ALLOW_REFRESH_TOKEN_AUTH",
    "ALLOW_USER_SRP_AUTH",
  ]
}

resource "aws_ssm_parameter" "external_client_id" {
  count = local.create_external ? 1 : 0

  name  = "/${var.project}/platform-apps/${var.app_name}/cognito-external-client-id"
  type  = "String"
  value = aws_cognito_user_pool_client.external[0].id
}

resource "aws_ssm_parameter" "external_client_secret" {
  count = local.create_external && var.generate_secret ? 1 : 0

  name  = "/${var.project}/platform-apps/${var.app_name}/cognito-external-client-secret"
  type  = "SecureString"
  value = aws_cognito_user_pool_client.external[0].client_secret
}

################################################################################
# IAM Policy Document — grants ssm:GetParameter[s] over the parameters created.
# Attach to the app's task role via the module's `access_policy_json` output.
################################################################################

locals {
  ssm_arns = concat(
    local.create_internal ? [aws_ssm_parameter.internal_client_id[0].arn] : [],
    local.create_internal && var.generate_secret ? [aws_ssm_parameter.internal_client_secret[0].arn] : [],
    local.create_external ? [aws_ssm_parameter.external_client_id[0].arn] : [],
    local.create_external && var.generate_secret ? [aws_ssm_parameter.external_client_secret[0].arn] : [],
  )
}

data "aws_iam_policy_document" "access" {
  statement {
    sid       = "CognitoSSMRead"
    effect    = "Allow"
    actions   = ["ssm:GetParameter", "ssm:GetParameters"]
    resources = local.ssm_arns
  }
}
