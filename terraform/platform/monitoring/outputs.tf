output "alerts_topic_arn" {
  description = "ARN of the alerts SNS topic (cost anomalies, budgets)"
  value       = aws_sns_topic.alerts.arn
}

output "security_topic_arn" {
  description = "ARN of the security SNS topic (IAM changes, GuardDuty, Config, Security Hub)"
  value       = aws_sns_topic.security.arn
}

output "alert_dedup_table_name" {
  description = "DynamoDB table name for alert deduplication"
  value       = aws_dynamodb_table.alert_dedup.name
}

output "alert_dedup_table_arn" {
  description = "DynamoDB table ARN for alert deduplication"
  value       = aws_dynamodb_table.alert_dedup.arn
}
