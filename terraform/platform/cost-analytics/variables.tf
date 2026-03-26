variable "project" {
  description = "Project name used for resource naming"
  type        = string
}

variable "region" {
  description = "AWS region for Glue/Athena resources"
  type        = string
}

variable "aws_account_id" {
  description = "AWS account ID"
  type        = string
}

variable "alerts_topic_arn" {
  description = "ARN of the javabin-alerts SNS topic for budget notifications"
  type        = string
}

variable "billing_alarm_threshold_usd" {
  description = "CloudWatch billing alarm threshold in USD"
  type        = number
  default     = 200
}

variable "account_budget_usd" {
  description = "Account-level monthly budget in USD — auto-deny at 100%"
  type        = number
  default     = 500
}
