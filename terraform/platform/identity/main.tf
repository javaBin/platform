################################################################################
# Cognito User Pools — Internal (java.no) + External (public)
#
# Pools are always created. Google IdP is added when google_client_id is set.
# Custom domains require certificate_arn (must be in us-east-1 for CloudFront).
#
# Identity Center lives in terraform/org/ (human-applied, not CI).
################################################################################

################################################################################
# Internal Pool — Javabin heroes (@java.no accounts only)
################################################################################

resource "aws_cognito_user_pool" "internal" {
  name = "${var.project}-internal"

  lambda_config {
    pre_token_generation_config {
      lambda_arn     = aws_lambda_function.auth_claims.arn
      lambda_version = "V2_0"
    }
  }

  auto_verified_attributes = ["email"]
  username_attributes      = ["email"]

  # No self sign-up: users arrive through the federated IdP (or are created
  # by an admin). auth-claims also refuses non-@java.no accounts.
  admin_create_user_config {
    allow_admin_create_user_only = true
  }

  password_policy {
    minimum_length    = 12
    require_lowercase = true
    require_numbers   = true
    require_symbols   = false
    require_uppercase = true
  }

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true

    string_attribute_constraints {
      min_length = 1
      max_length = 256
    }
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  tags = {
    Name = "${var.project}-internal"
  }
}

resource "aws_cognito_identity_provider" "internal_google" {
  count = var.google_client_id != "" ? 1 : 0

  user_pool_id  = aws_cognito_user_pool.internal.id
  provider_name = "Google"
  provider_type = "Google"

  provider_details = {
    client_id                     = var.google_client_id
    client_secret                 = var.google_client_secret
    authorize_scopes              = "openid email profile"
    attributes_url                = "https://people.googleapis.com/v1/people/me?personFields="
    attributes_url_add_attributes = "true"
    # TODO: hd= is a UI hint only, not server-side enforcement. Add a pre-sign-up
    # Lambda trigger to validate @java.no domain, or use separate GCP OAuth clients.
    authorize_url        = "https://accounts.google.com/o/oauth2/v2/auth?hd=${var.google_workspace_domain}"
    oidc_issuer          = "https://accounts.google.com"
    token_url            = "https://www.googleapis.com/oauth2/v4/token"
    token_request_method = "POST"
  }

  attribute_mapping = {
    email    = "email"
    username = "sub"
    name     = "name"
  }
}

# Cognito domain — needed for SAML ACS URL and hosted UI
resource "aws_cognito_user_pool_domain" "internal" {
  domain       = "${var.project}-internal"
  user_pool_id = aws_cognito_user_pool.internal.id
}

# Cognito groups are managed by the team provisioner (from groups.yaml and
# team YAMLs), not by Terraform. The provisioner creates them dynamically.

# Remove old Terraform-managed groups from state without deleting from Cognito.
removed {
  from = aws_cognito_user_group.internal_groups
  lifecycle { destroy = false }
}

################################################################################
# External Pool — Public users (JavaZone attendees, etc.)
################################################################################

resource "aws_cognito_user_pool" "external" {
  name = "${var.project}-external"

  auto_verified_attributes = ["email"]
  username_attributes      = ["email"]

  password_policy {
    minimum_length    = 8
    require_lowercase = true
    require_numbers   = true
    require_symbols   = false
    require_uppercase = false
  }

  schema {
    name                = "email"
    attribute_data_type = "String"
    required            = true
    mutable             = true

    string_attribute_constraints {
      min_length = 1
      max_length = 256
    }
  }

  account_recovery_setting {
    recovery_mechanism {
      name     = "verified_email"
      priority = 1
    }
  }

  tags = {
    Name = "${var.project}-external"
  }
}

resource "aws_cognito_identity_provider" "external_google" {
  count = var.google_client_id != "" ? 1 : 0

  user_pool_id  = aws_cognito_user_pool.external.id
  provider_name = "Google"
  provider_type = "Google"

  provider_details = {
    client_id                     = var.google_client_id
    client_secret                 = var.google_client_secret
    authorize_scopes              = "openid email profile"
    attributes_url                = "https://people.googleapis.com/v1/people/me?personFields="
    attributes_url_add_attributes = "true"
    authorize_url                 = "https://accounts.google.com/o/oauth2/v2/auth"
    oidc_issuer                   = "https://accounts.google.com"
    token_url                     = "https://www.googleapis.com/oauth2/v4/token"
    token_request_method          = "POST"
  }

  attribute_mapping = {
    email    = "email"
    username = "sub"
    name     = "name"
  }
}

resource "aws_cognito_user_pool_domain" "external" {
  count = var.certificate_arn != "" ? 1 : 0

  domain          = "auth-external.${var.domain}"
  certificate_arn = var.certificate_arn
  user_pool_id    = aws_cognito_user_pool.external.id
}

resource "aws_route53_record" "external_auth" {
  count = var.route53_zone_id != "" && var.certificate_arn != "" ? 1 : 0

  zone_id = var.route53_zone_id
  name    = "auth-external.${var.domain}"
  type    = "A"

  alias {
    name                   = aws_cognito_user_pool_domain.external[0].cloudfront_distribution
    zone_id                = aws_cognito_user_pool_domain.external[0].cloudfront_distribution_zone_id
    evaluate_target_health = false
  }
}

################################################################################
# Registry group claims — javabin-auth-claims (pre-token-generation V2)
#
# Federated users (GoogleSAML) arrive with only an email; no Cognito group
# membership exists for them. team-provisioner writes registry membership
# (groups.yaml with cognito: true, team-<name> groups) to the memberships
# table; this trigger puts the user's groups into cognito:groups of the ID
# and access token at every sign-in and refresh.
#
# The same table holds each app client's allowed groups (written by
# app-auth-sync from app.yaml auth.groups). If a client has allowed groups
# and the user is in none of them, the trigger refuses the token.
################################################################################

locals {
  lambda_src_path = "${path.module}/../../lambda-src"
}

resource "aws_dynamodb_table" "auth_memberships" {
  name         = "${var.project}-auth-memberships"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"

  attribute {
    name = "pk"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  tags = {
    Name = "${var.project}-auth-memberships"
  }
}

data "archive_file" "auth_claims" {
  type             = "zip"
  output_path      = "${path.module}/builds/auth_claims.zip"
  output_file_mode = "0644"
  source_dir       = "${local.lambda_src_path}/auth_claims"
}

resource "aws_iam_role" "auth_claims" {
  name = "${var.project}-auth-claims"
  # The org boundary denies creating any role without one
  permissions_boundary = var.org_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "auth_claims" {
  name = "${var.project}-auth-claims"
  role = aws_iam_role.auth_claims.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "ReadMemberships"
      Effect   = "Allow"
      Action   = ["dynamodb:Scan", "dynamodb:GetItem"]
      Resource = aws_dynamodb_table.auth_memberships.arn
    }]
  })
}

resource "aws_iam_role_policy_attachment" "auth_claims_logs" {
  role       = aws_iam_role.auth_claims.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "auth_claims" {
  function_name    = "${var.project}-auth-claims"
  role             = aws_iam_role.auth_claims.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 5
  memory_size      = 256
  filename         = data.archive_file.auth_claims.output_path
  source_code_hash = data.archive_file.auth_claims.output_base64sha256

  environment {
    variables = {
      MEMBERSHIPS_TABLE = aws_dynamodb_table.auth_memberships.name
      # Internal pool only: refuse tokens for anything but verified
      # @<workspace domain> accounts. Users from these IdPs count as verified.
      REQUIRED_EMAIL_DOMAIN        = var.google_workspace_domain
      FEDERATED_IDENTITY_PROVIDERS = join(",", var.internal_identity_providers)
    }
  }
}

resource "aws_lambda_permission" "auth_claims_cognito" {
  statement_id  = "AllowCognitoInternalPool"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.auth_claims.function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.internal.arn
}
