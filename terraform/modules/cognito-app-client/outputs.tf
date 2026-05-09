################################################################################
# User pool metadata (looked up by name, exposed for app config)
################################################################################

output "internal_user_pool_id" {
  description = "ID of the internal Cognito user pool (empty if pool != internal/both)"
  value       = local.internal_pool_id
}

output "internal_user_pool_arn" {
  description = "ARN of the internal Cognito user pool"
  value       = local.internal_pool_arn
}

output "internal_user_pool_domain" {
  description = "Hosted-UI FQDN for the internal pool (empty if pool != internal/both)"
  value       = local.internal_pool_domain
}

output "internal_issuer_url" {
  description = "OIDC issuer URL for the internal pool — for backend JWT validation"
  value       = local.internal_issuer_url
}

output "external_user_pool_id" {
  description = "ID of the external Cognito user pool (empty if pool != external/both)"
  value       = local.external_pool_id
}

output "external_user_pool_arn" {
  description = "ARN of the external Cognito user pool"
  value       = local.external_pool_arn
}

output "external_user_pool_domain" {
  description = "Custom domain for the external pool (empty when no custom domain configured)"
  value       = local.external_pool_domain
}

output "external_issuer_url" {
  description = "OIDC issuer URL for the external pool"
  value       = local.external_issuer_url
}

################################################################################
# Client credentials
################################################################################

output "internal_client_id" {
  description = "Internal Cognito app client ID (empty if pool != internal/both)"
  value       = local.create_internal ? aws_cognito_user_pool_client.internal[0].id : ""
}

output "external_client_id" {
  description = "External Cognito app client ID (empty if pool != external/both)"
  value       = local.create_external ? aws_cognito_user_pool_client.external[0].id : ""
}

output "internal_client_id_arn" {
  description = "ARN of the SSM parameter holding the internal client ID (empty when pool != internal/both)"
  value       = local.create_internal ? aws_ssm_parameter.internal_client_id[0].arn : ""
}

output "internal_client_secret_arn" {
  description = "ARN of the SSM parameter holding the internal client secret (empty when not generated)"
  value       = local.create_internal && var.generate_secret ? aws_ssm_parameter.internal_client_secret[0].arn : ""
}

output "external_client_id_arn" {
  description = "ARN of the SSM parameter holding the external client ID (empty when pool != external/both)"
  value       = local.create_external ? aws_ssm_parameter.external_client_id[0].arn : ""
}

output "external_client_secret_arn" {
  description = "ARN of the SSM parameter holding the external client secret (empty when not generated)"
  value       = local.create_external && var.generate_secret ? aws_ssm_parameter.external_client_secret[0].arn : ""
}

################################################################################
# IAM
################################################################################

output "ssm_parameter_arns" {
  description = "All SSM parameters created — kept for callers that want the bag (IAM policy scoping, etc.)"
  value       = local.ssm_arns
}

output "access_policy_json" {
  description = "IAM policy JSON granting ssm:GetParameter[s] over the cognito SSM params — attach to task role"
  value       = data.aws_iam_policy_document.access.json
}
