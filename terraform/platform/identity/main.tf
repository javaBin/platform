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

  auto_verified_attributes = ["email"]
  username_attributes      = ["email"]

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

# Groups mirroring Google Workspace (managed by group provisioner from groups.yaml)
# These are the base groups — the provisioner creates additional ones as needed.
resource "aws_cognito_user_group" "internal_groups" {
  for_each = toset(["helter", "styret", "drift", "pkom", "developers"])

  name         = each.key
  user_pool_id = aws_cognito_user_pool.internal.id
  description  = "${each.key} group (synced from Google Workspace)"
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
