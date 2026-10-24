output "security_group_id" {
  description = "The app's task security group"
  value       = aws_security_group.tasks.id
  # The ECS service switches to this group only once the ALB can reach it
  depends_on = [aws_vpc_security_group_ingress_rule.alb, aws_vpc_security_group_egress_rule.all]
}

output "registry_arn" {
  description = "Cloud Map service ARN for the ECS service's service_registries"
  value       = aws_service_discovery_service.this.arn
}

output "dns_name" {
  description = "Private DNS name peers use to reach this app"
  value       = "${var.team}-${var.name}.${var.project}.internal"
}

output "request_parameter_name" {
  description = "SSM parameter holding the app-to-app request"
  value       = aws_ssm_parameter.network.name
}

output "peer_url_arns" {
  description = "\"team/app\" => ARN of the parameter app-peers-sync writes the peer's URL to"
  value       = { for k in keys(var.calls) : k => "${local.param_arn_prefix}/peer-${replace(k, "/", "-")}-url" }
}

output "m2m_client_id_arn" {
  description = "ARN of the parameter app-peers-sync writes the M2M client ID to"
  value       = "${local.param_arn_prefix}/m2m-client-id"
}

output "m2m_client_secret_arn" {
  description = "ARN of the parameter app-peers-sync writes the M2M client secret to"
  value       = "${local.param_arn_prefix}/m2m-client-secret"
}

output "token_url" {
  description = "OAuth2 token endpoint of the internal pool (client_credentials)"
  value       = "https://${var.internal_pool_name}.auth.${local.region}.amazoncognito.com/oauth2/token"
}

output "issuer_url" {
  description = "Issuer of M2M access tokens (empty when the app uses no scopes)"
  value       = local.issuer_url
}

output "jwks_url" {
  description = "JWKS URL for verifying M2M access tokens (empty when the app uses no scopes)"
  value       = local.uses_scopes ? "${local.issuer_url}/.well-known/jwks.json" : ""
}

output "scope_prefix" {
  description = "Prefix of this app's scopes in access tokens"
  value       = "${var.team}-${var.name}/"
}
