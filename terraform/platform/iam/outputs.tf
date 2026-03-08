output "ecs_execution_role_arn" {
  description = "ARN of the ECS execution role"
  value       = aws_iam_role.ecs_execution.arn
}

output "ci_infra_role_arn" {
  description = "ARN of the platform CI infra role"
  value       = aws_iam_role.ci_infra.arn
}

output "ci_app_role_arns" {
  description = "Map of repo name to CI app role ARN"
  value       = { for repo, role in aws_iam_role.ci_app : repo => role.arn }
}

output "ci_deploy_role_arns" {
  description = "Map of repo name to CI deploy role ARN"
  value       = { for repo, role in aws_iam_role.ci_deploy : repo => role.arn }
}

output "ci_override_approver_role_arn" {
  description = "ARN of the CI override approver role"
  value       = aws_iam_role.ci_override_approver.arn
}

output "ci_registry_role_arn" {
  description = "ARN of the registry CI role"
  value       = aws_iam_role.ci_registry.arn
}

output "github_oidc_provider_arn" {
  description = "ARN of the GitHub OIDC provider (data source)"
  value       = data.aws_iam_openid_connect_provider.github.arn
}

output "developer_boundary_arn" {
  description = "ARN of the javabin-developer-boundary IAM policy"
  value       = aws_iam_policy.developer_boundary.arn
}
