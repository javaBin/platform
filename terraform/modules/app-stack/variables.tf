variable "config_file" {
  description = "Path to app.yaml relative to the Terraform root module"
  type        = string
  default     = "../app.yaml"
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
