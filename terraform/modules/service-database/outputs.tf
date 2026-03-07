output "table_name" {
  description = "DynamoDB table name"
  value       = aws_dynamodb_table.this.name
}

output "table_arn" {
  description = "DynamoDB table ARN"
  value       = aws_dynamodb_table.this.arn
}

output "access_policy_json" {
  description = "IAM policy JSON granting CRUD access — attach to task role"
  value       = data.aws_iam_policy_document.access.json
}
