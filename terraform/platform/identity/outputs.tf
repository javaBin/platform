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
