output "slack_alert_function_arn" {
  description = "ARN of the slack-alert Lambda function"
  value       = aws_lambda_function.slack_alert.arn
}

output "cost_report_function_arn" {
  description = "ARN of the cost-report Lambda function"
  value       = aws_lambda_function.cost_report.arn
}

output "daily_cost_check_function_arn" {
  description = "ARN of the daily-cost-check Lambda function"
  value       = aws_lambda_function.daily_cost_check.arn
}

output "compliance_reporter_function_arn" {
  description = "ARN of the compliance-reporter Lambda function"
  value       = aws_lambda_function.compliance_reporter.arn
}

output "override_cleanup_function_arn" {
  description = "ARN of the override-cleanup Lambda function"
  value       = aws_lambda_function.override_cleanup.arn
}

output "team_provisioner_function_arn" {
  description = "ARN of the team-provisioner Lambda function"
  value       = aws_lambda_function.team_provisioner.arn
}

output "apply_gate_role_arn" {
  description = "ARN of the apply-gate Lambda IAM role"
  value       = aws_iam_role.apply_gate.arn
}
