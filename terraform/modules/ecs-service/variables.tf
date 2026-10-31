variable "name" {
  description = "Service name"
  type        = string
}

variable "team" {
  description = "Team name, used as resource name prefix for team-scoped isolation"
  type        = string
}

variable "cluster_id" {
  description = "ECS cluster ID"
  type        = string
}

variable "image" {
  description = "Container image of the Terraform-managed revision (the ECR repo URL). ecs-deploy replaces it with the digest built from the commit"
  type        = string
}

variable "cpu" {
  description = "CPU units for the task (256, 512, 1024, etc.)"
  type        = number
  default     = 512
}

variable "memory" {
  description = "Memory in MiB for the task"
  type        = number
  default     = 1024
}

variable "port" {
  description = "Container port"
  type        = number
  default     = 8000
}

variable "health_check_path" {
  description = "Health check endpoint path"
  type        = string
  default     = "/health"
}

variable "environment" {
  description = "Environment variables for the container"
  type        = map(string)
  default     = {}
}

variable "secrets" {
  description = "Secrets from SSM Parameter Store (name → ARN)"
  type        = map(string)
  default     = {}
}

variable "execution_role_arn" {
  description = "ECS task execution role ARN"
  type        = string
}

variable "task_role_arn" {
  description = "ECS task role ARN"
  type        = string
}

variable "subnet_ids" {
  description = "Subnet IDs for the ECS service"
  type        = list(string)
}

variable "security_group_ids" {
  description = "Security group IDs for the ECS service"
  type        = list(string)
}

variable "target_group_arn" {
  description = "ALB target group ARN. Empty for services without routing (workers)."
  type        = string
  default     = ""
}

variable "service_registry_arns" {
  description = "Cloud Map service ARNs to register the tasks in (at most one; from app-network)"
  type        = list(string)
  default     = []

  validation {
    condition     = length(var.service_registry_arns) <= 1
    error_message = "ECS Fargate services support at most one service registry."
  }
}

variable "desired_count" {
  description = "Number of tasks to run. Applied by the ecs-deploy workflow (from the service tag javabin:desired-count), not by Terraform"
  type        = number
  default     = 1
}

variable "region" {
  description = "AWS region for CloudWatch logs"
  type        = string
}

variable "container_user" {
  description = "User to run the container as (non-root for ECS.20 compliance)"
  type        = string
  default     = "1000"
}

variable "readonly_root_filesystem" {
  description = "Mount the container root filesystem read-only (ECS.5). /tmp is always writable."
  type        = bool
  default     = true
}
