output "alerts_topic_arn" {
  description = "ARN of the alerts SNS topic (cost anomalies, budgets)"
  value       = aws_sns_topic.alerts.arn
}

output "security_topic_arn" {
  description = "ARN of the security SNS topic (IAM changes, GuardDuty, Config, Security Hub)"
  value       = aws_sns_topic.security.arn
}
