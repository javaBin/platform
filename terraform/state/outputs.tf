output "state_bucket_name" {
  description = "Name of the Terraform state S3 bucket"
  value       = aws_s3_bucket.state.id
}

output "state_bucket_arn" {
  description = "ARN of the Terraform state S3 bucket"
  value       = aws_s3_bucket.state.arn
}

output "infra_lock_table_name" {
  description = "Name of the infra DynamoDB lock table"
  value       = aws_dynamodb_table.infra_lock.name
}

output "app_lock_table_name" {
  description = "Name of the app DynamoDB lock table"
  value       = aws_dynamodb_table.app_locks.name
}

output "plan_artifacts_bucket_name" {
  description = "Name of the CI plan artifacts S3 bucket"
  value       = aws_s3_bucket.plan_artifacts.id
}

output "plan_artifacts_bucket_arn" {
  description = "ARN of the CI plan artifacts S3 bucket"
  value       = aws_s3_bucket.plan_artifacts.arn
}
