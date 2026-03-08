variable "name" {
  description = "Service name (e.g. 'moresleep'). Role name: {project}-{name}"
  type        = string
}

variable "project" {
  description = "Platform project name (e.g. 'javabin')"
  type        = string
}

variable "region" {
  description = "AWS region for log group ARN scoping"
  type        = string
}

variable "aws_account_id" {
  description = "AWS account ID for ARN construction"
  type        = string
}

variable "permissions_boundary_arn" {
  description = "ARN of the permission boundary to attach to the role (required by self-replicating boundary)"
  type        = string
}

variable "additional_policy_jsons" {
  description = "Map of policy name => IAM policy JSON to attach (e.g. from service-bucket or service-database access_policy_json outputs)"
  type        = map(string)
  default     = {}
}
