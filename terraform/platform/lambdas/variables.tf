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

variable "internal_user_pool_id" {
  description = "Cognito internal user pool ID"
  type        = string
}

variable "internal_user_pool_arn" {
  description = "Cognito internal user pool ARN"
  type        = string
}

variable "identity_store_id" {
  description = "IAM Identity Center identity store ID"
  type        = string
  default     = ""
}

