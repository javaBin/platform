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

variable "registered_app_repos" {
  description = "List of GitHub repo names (without org prefix) that get CI app + deploy roles"
  type        = list(string)
  default     = []
}

variable "override_approvers" {
  description = "GitHub usernames allowed to approve risk gate overrides"
  type        = list(string)
  default     = ["alexander-amiri"]
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
