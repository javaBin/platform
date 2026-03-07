output "role_arn" {
  description = "Task role ARN (pass to ecs-service module)"
  value       = aws_iam_role.this.arn
}

output "role_name" {
  description = "Task role name"
  value       = aws_iam_role.this.name
}

output "role_id" {
  description = "Task role ID (for attaching additional inline policies outside the module)"
  value       = aws_iam_role.this.id
}
