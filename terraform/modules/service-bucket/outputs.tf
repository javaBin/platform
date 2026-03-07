output "bucket_name" {
  description = "S3 bucket name"
  value       = aws_s3_bucket.this.id
}

output "bucket_arn" {
  description = "S3 bucket ARN"
  value       = aws_s3_bucket.this.arn
}

output "access_policy_json" {
  description = "IAM policy JSON granting read/write access — attach to task role"
  value       = data.aws_iam_policy_document.access.json
}
