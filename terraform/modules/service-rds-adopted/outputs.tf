output "endpoint" {
  description = "RDS instance endpoint (host:port)"
  value       = aws_db_instance.this.endpoint
}

output "address" {
  description = "RDS instance hostname"
  value       = aws_db_instance.this.address
}

output "port" {
  description = "RDS instance port, as a string for environment variables"
  value       = tostring(aws_db_instance.this.port)
}

output "arn" {
  description = "RDS instance ARN"
  value       = aws_db_instance.this.arn
}

output "resource_id" {
  description = "RDS instance resource ID (dbi-resource-id, used in rds-db:connect ARNs)"
  value       = aws_db_instance.this.resource_id
}
