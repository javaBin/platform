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

variable "alb_http_listener_arn" {
  description = "ARN of the platform ALB's port-80 redirect listener (Security Hub ELB.18 suppression)"
  type        = string
}

variable "registered_teams" {
  description = "Teams with app task definitions (Security Hub ECS.20 suppression for the /tmp init container)"
  type        = list(string)
  default     = []
}
