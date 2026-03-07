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
  description = "Custom domain of the internal Cognito user pool (empty if not configured)"
  value       = var.certificate_arn != "" ? "auth-internal.${var.domain}" : ""
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
