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

output "well_known_url" {
  description = "OIDC discovery document URL"
  value       = "https://cognito-idp.${local.region}.amazonaws.com/${local.pool_id}/.well-known/openid-configuration"
}

output "client_id_arn" {
  description = "ARN of the SSM parameter the platform writes the client ID to"
  value       = "${local.param_arn}/cognito-client-id"
}

output "client_secret_arn" {
  description = "ARN of the SSM parameter the platform writes the client secret to"
  value       = "${local.param_arn}/cognito-client-secret"
}

output "identity_provider_arn" {
  description = "ARN of the SSM parameter the platform writes the client's sign-in IdP to (e.g. Google, GoogleSAML, COGNITO)"
  value       = "${local.param_arn}/cognito-client-identity-provider"
}

output "auth_status_parameter_name" {
  description = "SSM parameter where the platform reports the sync result (JSON; read by scripts/auth-doctor.py)"
  value       = "${local.param_prefix}/auth-status"
}

output "request_parameter_name" {
  description = "SSM parameter holding the client request (empty for client_type = none)"
  value       = one(aws_ssm_parameter.auth_request[*].name)
}
