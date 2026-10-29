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

variable "github_org_id" {
  description = "GitHub organization ID, used in immutable OIDC subjects (repo:<org>@<org_id>/<repo>@<repo_id>:...)"
  type        = string
  default     = "95047"
}

variable "apply_gate_role_arn" {
  description = "ARN of the apply-gate Lambda role (for team role trust policy)"
  type        = string
}

variable "ci_broker_role_arn" {
  description = "ARN of the CI broker Lambda role (for team/deploy role trust policy)"
  type        = string
}

variable "ci_broker_function_arn" {
  description = "ARN of the CI broker Lambda function (for OIDC broker role invoke permission)"
  type        = string
}

variable "plan_review_function_arn" {
  description = "ARN of the plan review Lambda (broker role may invoke it)"
  type        = string
}
