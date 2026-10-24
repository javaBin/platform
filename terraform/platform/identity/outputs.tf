################################################################################
# Cognito outputs
################################################################################

# Internal pool
output "internal_user_pool_id" {
  description = "ID of the internal Cognito user pool"
  value       = aws_cognito_user_pool.internal.id
}

output "internal_user_pool_arn" {
  description = "ARN of the internal Cognito user pool"
  value       = aws_cognito_user_pool.internal.arn
}

output "internal_user_pool_domain" {
  description = "Cognito-managed domain for the internal pool (SAML ACS URL base)"
  value       = "${var.project}-internal.auth.eu-central-1.amazoncognito.com"
}

output "internal_saml_acs_url" {
  description = "SAML ACS URL for Google Workspace SAML app configuration"
  value       = "https://${var.project}-internal.auth.eu-central-1.amazoncognito.com/saml2/idpresponse"
}

output "internal_saml_entity_id" {
  description = "SAML Entity ID (SP) for Google Workspace SAML app configuration"
  value       = "urn:amazon:cognito:sp:${aws_cognito_user_pool.internal.id}"
}

output "internal_google_enabled" {
  description = "Whether the internal pool's Google OIDC IdP exists (its OAuth client is in SSM)"
  value       = local.google_internal_ready
}

# External pool
output "external_user_pool_id" {
  description = "ID of the external Cognito user pool"
  value       = aws_cognito_user_pool.external.id
}

output "external_user_pool_arn" {
  description = "ARN of the external Cognito user pool"
  value       = aws_cognito_user_pool.external.arn
}

output "external_user_pool_domain" {
  description = "Custom domain of the external Cognito user pool (empty if not configured)"
  value       = var.certificate_arn != "" ? "auth-external.${var.domain}" : ""
}

output "auth_memberships_table_name" {
  description = "DynamoDB table with registry group memberships and per-client allowed groups"
  value       = aws_dynamodb_table.auth_memberships.name
}

output "auth_memberships_table_arn" {
  description = "ARN of the auth memberships table"
  value       = aws_dynamodb_table.auth_memberships.arn
}

output "internal_identity_providers" {
  description = "Sign-in IdPs app-auth-sync puts on internal-pool clients"
  value       = var.internal_identity_providers

  # Google is trusted by auth-claims only with the domain rule in place and
  # only once the IdP exists (else app-auth-sync would skip every request).
  precondition {
    condition     = !contains(var.internal_identity_providers, "Google") || (local.google_internal_ready && var.google_workspace_domain != "")
    error_message = "internal_identity_providers names Google, but /<project>/platform/google-oauth/internal/client-id and client-secret are not in SSM yet, or google_workspace_domain is empty (auth-claims' REQUIRED_EMAIL_DOMAIN). See docs/cognito-google-setup.md."
  }
}

output "external_identity_providers" {
  description = "Sign-in IdPs app-auth-sync puts on external-pool clients"
  value       = var.external_identity_providers
}
