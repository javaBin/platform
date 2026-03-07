variable "project" {
  description = "Project name used for resource naming"
  type        = string
}

variable "region" {
  description = "AWS region"
  type        = string
}

variable "aws_account_id" {
  description = "AWS account ID"
  type        = string
}

variable "alerts_topic_arn" {
  description = "ARN of the javabin-alerts SNS topic (cost anomalies, budgets)"
  type        = string
}

variable "security_topic_arn" {
  description = "ARN of the javabin-security SNS topic (IAM, GuardDuty, Config, Security Hub)"
  type        = string
}

variable "compliance_reporter_identities" {
  description = "IAM identity substrings allowed to trigger compliance reporting"
  type        = list(string)
}

