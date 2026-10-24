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

variable "account_budget_usd" {
  description = "Account-level monthly budget in USD, tax included (Cost Explorer UnblendedCost incl. 25% VAT). Notifies on forecast >100%, actual >100% and actual >150%. Steady state is ~$550/mo (2026-10); lower this as the legacy cleanup in docs/cost-management.md lands."
  type        = number
  default     = 600
}

variable "org_boundary_arn" {
  description = "ARN of the org permission boundary (required for all IAM roles)"
  type        = string
}
