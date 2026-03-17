output "endpoint" {
  description = "RDS instance endpoint"
  value       = aws_db_instance.this.endpoint
}

output "port" {
  description = "RDS instance port"
  value       = aws_db_instance.this.port
}

output "db_name" {
  description = "Database name"
  value       = aws_db_instance.this.db_name
}

output "password_parameter_arn" {
  description = "SSM parameter ARN for the master password"
  value       = aws_ssm_parameter.master_password.arn
}

output "access_policy_json" {
  description = "IAM policy JSON granting access to this database"
  value       = data.aws_iam_policy_document.access.json
}

output "security_group_id" {
  description = "Security group ID for the RDS instance"
  value       = aws_security_group.this.id
}
