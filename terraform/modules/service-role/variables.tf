variable "name" {
  description = "Service name (e.g. 'moresleep'). Role name: {team}-{name}"
  type        = string
}

variable "team" {
  description = "Team name, used as resource name prefix for team-scoped isolation"
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

variable "trusted_services" {
  description = "AWS service principals allowed to assume this role (e.g. ecs-tasks, ec2, lambda)"
  type        = list(string)
  default     = ["ecs-tasks.amazonaws.com"]
}

variable "additional_policy_jsons" {
  description = "Map of policy name => IAM policy JSON to attach (e.g. from service-bucket or service-database access_policy_json outputs)"
  type        = map(string)
  default     = {}
}
