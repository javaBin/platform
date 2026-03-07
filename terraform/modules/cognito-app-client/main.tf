################################################################################
# Cognito App Client — registers an app with internal/external user pools
#
# Creates Cognito user pool clients and stores credentials in SSM so the
# app can read them at runtime without hardcoding.
################################################################################

locals {
  create_internal = var.pool == "internal" || var.pool == "both"
  create_external = var.pool == "external" || var.pool == "both"
}

################################################################################
# Internal Pool Client
################################################################################

resource "aws_cognito_user_pool_client" "internal" {
  count = local.create_internal ? 1 : 0

  name         = var.app_name
  user_pool_id = var.internal_user_pool_id

  lifecycle {
    precondition {
      condition     = var.internal_user_pool_id != ""
      error_message = "internal_user_pool_id must be set when pool is \"internal\" or \"both\"."
    }
  }

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

  name         = var.app_name
  user_pool_id = var.external_user_pool_id

  lifecycle {
    precondition {
      condition     = var.external_user_pool_id != ""
      error_message = "external_user_pool_id must be set when pool is \"external\" or \"both\"."
    }
  }

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
