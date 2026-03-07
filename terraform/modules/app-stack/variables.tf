variable "config_file" {
  description = "Path to app.yaml relative to the Terraform root module"
  type        = string
  default     = "../app.yaml"
}

variable "environment_name" {
  description = "Environment name (e.g. 'dev', 'prod'). Empty string for single-environment apps."
  type        = string
  default     = ""

  validation {
    condition     = var.environment_name == "" || can(regex("^[a-z][a-z0-9]{0,3}$", var.environment_name))
    error_message = "environment_name must be lowercase alphanumeric, max 4 chars (e.g. dev, prod, test)."
  }
}

variable "create_ecr" {
  description = "Whether to create the ECR repository. Set false for secondary environments sharing an ECR repo."
  type        = bool
  default     = true
}

variable "aws_account_id" {
  description = "AWS account ID for ARN construction and bucket naming"
  type        = string
}

variable "project" {
  description = "Platform project name (e.g. 'javabin')"
  type        = string
  default     = "javabin"
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "eu-central-1"
}

variable "image_tag" {
  description = "Container image tag (default: 'latest'). CI overrides via TF_VAR_image_tag."
  type        = string
  default     = "latest"
}

variable "additional_environment" {
  description = "Additional environment variables merged with YAML-defined ones (overrides on conflict)"
  type        = map(string)
  default     = {}
}

variable "additional_secrets" {
  description = "Additional secrets merged with YAML-defined ones (env_var_name => secret_arn)"
  type        = map(string)
  default     = {}
}

variable "additional_policy_jsons" {
  description = "Additional IAM policies attached to the task role (name => policy JSON)"
  type        = map(string)
  default     = {}
}
