output "parameter_arn" {
  description = "SSM parameter ARN (pass to ECS task definition secrets map)"
  value       = aws_ssm_parameter.this.arn
}

output "parameter_name" {
  description = "SSM parameter name"
  value       = aws_ssm_parameter.this.name
}

output "access_policy_json" {
  description = "IAM policy JSON granting read access — attach to task role"
  value       = data.aws_iam_policy_document.access.json
}
