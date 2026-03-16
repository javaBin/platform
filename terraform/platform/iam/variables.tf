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

variable "registered_teams" {
  description = "List of GitHub team slugs that get CI team + deploy roles"
  type        = list(string)
  default     = []
}

variable "github_org" {
  description = "GitHub organization name"
  type        = string
  default     = "javaBin"
}

variable "apply_gate_role_arn" {
  description = "ARN of the apply-gate Lambda role (for ci_app trust policy)"
  type        = string
}
