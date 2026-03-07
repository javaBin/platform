output "secret_arn" {
  description = "Secret ARN (pass to ECS task definition secrets map)"
  value       = aws_secretsmanager_secret.this.arn
}

output "secret_name" {
  description = "Secret name"
  value       = aws_secretsmanager_secret.this.name
}

output "access_policy_json" {
  description = "IAM policy JSON granting read access — attach to task role"
  value       = data.aws_iam_policy_document.access.json
}
