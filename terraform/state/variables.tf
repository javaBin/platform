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
