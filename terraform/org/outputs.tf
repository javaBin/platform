output "organization_id" {
  description = "ID of the AWS Organization"
  value       = aws_organizations_organization.main.id
}

output "organization_root_id" {
  description = "ID of the organization root"
  value       = aws_organizations_organization.main.roots[0].id
}

output "scp_id" {
  description = "ID of the require-permission-boundary SCP"
  value       = aws_organizations_policy.require_boundary.id
}
