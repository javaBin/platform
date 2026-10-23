output "user_pool_id" {
  description = "ID of the selected Cognito user pool"
  value       = local.pool_id
}

output "user_pool_domain" {
  description = "Hosted-UI FQDN of the selected pool (empty for external without a custom domain)"
  value       = local.pool_domain
}

output "issuer_url" {
  description = "OIDC issuer URL — for backend JWT validation"
  value       = "https://cognito-idp.${local.region}.amazonaws.com/${local.pool_id}"
}

output "client_id_arn" {
  description = "ARN of the SSM parameter the platform writes the client ID to"
  value       = "${local.param_arn}/cognito-client-id"
}

output "client_secret_arn" {
  description = "ARN of the SSM parameter the platform writes the client secret to"
  value       = "${local.param_arn}/cognito-client-secret"
}

output "request_parameter_name" {
  description = "SSM parameter holding the client request"
  value       = aws_ssm_parameter.auth_request.name
}
