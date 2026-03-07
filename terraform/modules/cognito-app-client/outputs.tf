output "internal_client_id" {
  description = "Internal Cognito app client ID (empty if pool != internal/both)"
  value       = local.create_internal ? aws_cognito_user_pool_client.internal[0].id : ""
}

output "external_client_id" {
  description = "External Cognito app client ID (empty if pool != external/both)"
  value       = local.create_external ? aws_cognito_user_pool_client.external[0].id : ""
}

output "ssm_parameter_arns" {
  description = "ARNs of all SSM parameters created (for IAM policy scoping)"
  value = concat(
    local.create_internal ? [aws_ssm_parameter.internal_client_id[0].arn] : [],
    local.create_internal && var.generate_secret ? [aws_ssm_parameter.internal_client_secret[0].arn] : [],
    local.create_external ? [aws_ssm_parameter.external_client_id[0].arn] : [],
    local.create_external && var.generate_secret ? [aws_ssm_parameter.external_client_secret[0].arn] : [],
  )
}
