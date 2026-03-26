variable "region" {
  description = "AWS region for all resources"
  type        = string
  default     = "eu-central-1"
}

variable "project" {
  description = "Project name used for resource naming"
  type        = string
  default     = "javabin"
}

variable "aws_account_id" {
  description = "AWS account ID"
  type        = string
  default     = "553637109631"
}

variable "environment" {
  description = "Environment name"
  type        = string
  default     = "production"
}

variable "boundary_policy_arns" {
  description = "ARNs of the permitted permission boundary policies (org + developer)"
  type        = list(string)
  default = [
    "arn:aws:iam::553637109631:policy/javabin-org-boundary",
    "arn:aws:iam::553637109631:policy/javabin-developer-boundary",
  ]
}

variable "exempt_roles" {
  description = "IAM role ARNs exempt from the SCP boundary requirement (platform CI + admin)"
  type        = list(string)
  default = [
    "arn:aws:iam::553637109631:role/javabin-ci-infra",
    "arn:aws:iam::553637109631:root"
  ]
}

variable "session_duration" {
  description = "SSO session duration in ISO 8601 format"
  type        = string
  default     = "PT4H"
}
