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

output "resource_tagger_function_arn" {
  description = "ARN of the resource-tagger Lambda function"
  value       = aws_lambda_function.resource_tagger.arn
}

output "override_cleanup_function_arn" {
  description = "ARN of the override-cleanup Lambda function"
  value       = aws_lambda_function.override_cleanup.arn
}

output "team_provisioner_function_arn" {
  description = "ARN of the team-provisioner Lambda function"
  value       = aws_lambda_function.team_provisioner.arn
}

output "password_set_function_arn" {
  description = "ARN of the password-set Lambda function"
  value       = aws_lambda_function.password_set.arn
}

output "password_set_function_url" {
  description = "Function URL for the password-set Lambda"
  value       = aws_lambda_function_url.password_set.function_url
}

output "apply_gate_role_arn" {
  description = "ARN of the apply-gate Lambda IAM role"
  value       = aws_iam_role.apply_gate.arn
}

output "ci_broker_role_arn" {
  description = "ARN of the CI broker Lambda IAM role"
  value       = aws_iam_role.ci_broker.arn
}

output "ci_broker_function_arn" {
  description = "ARN of the CI broker Lambda function"
  value       = aws_lambda_function.ci_broker.arn
}

output "budget_enforcer_function_arn" {
  description = "ARN of the budget-enforcer Lambda function"
  value       = aws_lambda_function.budget_enforcer.arn
}

output "budget_enforcement_topic_arn" {
  description = "ARN of the budget-enforcement SNS topic"
  value       = aws_sns_topic.budget_enforcement.arn
}
