################################################################################
# Cognito User Pools — Internal (java.no) + External (public)
#
# Pools are always created. The internal pool's Google IdP is added once its
# OAuth client is stored in SSM (see below); the external pool's when
# google_client_id is set. Which IdPs app clients use is declared per pool
# (internal_identity_providers / external_identity_providers), so adding an IdP
# changes no client until the list names it.
# Custom domains require certificate_arn (must be in us-east-1 for CloudFront).
#
# Identity Center lives in terraform/org/ (human-applied, not CI).
################################################################################

data "aws_region" "current" {}

################################################################################
# Internal Pool — Javabin heroes (@java.no accounts only)
################################################################################

resource "aws_cognito_user_pool" "internal" {
  name                = "${var.project}-internal"
  deletion_protection = "ACTIVE" # Cognito.6

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

# Google OIDC for the internal pool. The OAuth client belongs to a GCP project
# whose consent screen is Internal, so Google itself refuses non-java.no
# accounts, on a page that lets the user pick another account (the GoogleSAML
# app shows a dead-end 403 instead — javaBin/platform#157). Setup:
# docs/cognito-google-setup.md.
#
# An admin stores the client under /<project>/platform/google-oauth/internal/
# (client-id String, client-secret SecureString). Until both exist this
# creates nothing, so the code can merge before the credentials do.
data "aws_ssm_parameters_by_path" "google_internal" {
  path            = "/${var.project}/platform/google-oauth/internal"
  with_decryption = true
}

locals {
  google_internal_names = [for n in data.aws_ssm_parameters_by_path.google_internal.names : basename(n)]
  google_internal       = zipmap(local.google_internal_names, data.aws_ssm_parameters_by_path.google_internal.values)
  google_internal_ready = contains(local.google_internal_names, "client-id") && contains(local.google_internal_names, "client-secret")
}

resource "aws_cognito_identity_provider" "internal_google" {
  count = local.google_internal_ready ? 1 : 0

  user_pool_id  = aws_cognito_user_pool.internal.id
  provider_name = "Google"
  provider_type = "Google"

  provider_details = {
    client_id                     = local.google_internal["client-id"]
    client_secret                 = local.google_internal["client-secret"]
    authorize_scopes              = "openid email profile"
    attributes_url                = "https://people.googleapis.com/v1/people/me?personFields="
    attributes_url_add_attributes = "true"
    # Plain URL: Cognito drops query parameters from a Google-type
    # authorize_url (describe-identity-provider shows it without them), so
    # hd=/prompt= here only produced a perpetual plan diff. The account
    # chooser comes from prompt=select_account sent by the app (or the ALB)
    # to /oauth2/authorize, which managed login forwards to Google. See
    # docs/cognito-google-setup.md.
    authorize_url        = "https://accounts.google.com/o/oauth2/v2/auth"
    oidc_issuer          = "https://accounts.google.com"
    token_url            = "https://www.googleapis.com/oauth2/v4/token"
    token_request_method = "POST"
  }

  attribute_mapping = {
    email          = "email"
    email_verified = "email_verified"
    username       = "sub"
    name           = "name"
  }
}

# Cognito domain — needed for SAML ACS URL and hosted UI.
# managed_login_version: 1 = classic hosted UI, 2 = managed login. Only
# managed login forwards prompt=select_account to Google (the account
# chooser), but it has no sign-in page for a client without a branding
# style: app-auth-sync gives every app client one. Changing the version is an
# in-place update of the domain.
resource "aws_cognito_user_pool_domain" "internal" {
  domain                = "${var.project}-internal"
  user_pool_id          = aws_cognito_user_pool.internal.id
  managed_login_version = var.internal_managed_login_version
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
  name                = "${var.project}-external"
  deletion_protection = "ACTIVE" # Cognito.6

  # Native groups only — see auth_claims_external below
  lambda_config {
    pre_token_generation_config {
      lambda_arn     = aws_lambda_function.auth_claims_external.arn
      lambda_version = "V2_0"
    }
  }

  auto_verified_attributes = ["email"]
  username_attributes      = ["email"]

  # A changed email stays pending until the new address is verified, so apps
  # behind ALB sign-in never see an unverified address in the email claim.
  user_attribute_update_settings {
    attributes_require_verification_before_update = ["email"]
  }

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

# Cognito-managed prefix domain for the hosted UI. ALB sign-in from app.yaml
# routing.login and apps with auth: external point at it, so it is kept even
# when a custom domain is added below (a pool can have one of each).
resource "aws_cognito_user_pool_domain" "external_prefix" {
  domain       = "${var.project}-external"
  user_pool_id = aws_cognito_user_pool.external.id
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

# External pool: same code, but registry groups are never granted — anyone can
# sign up there with any email address. Native groups and per-client allowed
# groups still apply. A separate function (instead of the handler looking up
# which pool called it) keeps the pool -> Lambda dependency one-way.
resource "aws_lambda_function" "auth_claims_external" {
  function_name    = "${var.project}-auth-claims-external"
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
      REGISTRY_GROUPS   = "false"
    }
  }
}

resource "aws_lambda_permission" "auth_claims_external_cognito" {
  statement_id  = "AllowCognitoExternalPool"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.auth_claims_external.function_name
  principal     = "cognito-idp.amazonaws.com"
  source_arn    = aws_cognito_user_pool.external.arn
}
