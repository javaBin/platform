################################################################################
# Cognito outputs
################################################################################

# Internal pool
output "internal_user_pool_id" {
  description = "ID of the internal Cognito user pool (empty if Cognito disabled)"
  value       = var.enable_cognito ? aws_cognito_user_pool.internal[0].id : ""
}

output "internal_user_pool_arn" {
  description = "ARN of the internal Cognito user pool (empty if Cognito disabled)"
  value       = var.enable_cognito ? aws_cognito_user_pool.internal[0].arn : ""
}

output "internal_user_pool_domain" {
  description = "Custom domain of the internal Cognito user pool (empty if not configured)"
  value       = var.enable_cognito && var.certificate_arn != "" ? "auth-internal.${var.domain}" : ""
}

# External pool
output "external_user_pool_id" {
  description = "ID of the external Cognito user pool (empty if Cognito disabled)"
  value       = var.enable_cognito ? aws_cognito_user_pool.external[0].id : ""
}

output "external_user_pool_arn" {
  description = "ARN of the external Cognito user pool (empty if Cognito disabled)"
  value       = var.enable_cognito ? aws_cognito_user_pool.external[0].arn : ""
}

output "external_user_pool_domain" {
  description = "Custom domain of the external Cognito user pool (empty if not configured)"
  value       = var.enable_cognito && var.certificate_arn != "" ? "auth-external.${var.domain}" : ""
}
