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
  description = "User to run the app container as: user, uid, user:group or uid:gid. Non-root (ECS.20)."
  type        = string
  default     = "1000"

  validation {
    condition     = var.container_user != "" && !contains(["0", "root"], split(":", var.container_user)[0])
    error_message = "container_user must be a non-root user (not 0 or root)."
  }
}

variable "tmp_init_image" {
  description = "Image of the init container that makes /tmp writable (needs chmod). Pinned by digest; pulled through the NAT gateway."
  type        = string
  # busybox 1.37 (multi-arch index)
  default = "public.ecr.aws/docker/library/busybox@sha256:a7dbc319b1438115a207e11930dc4bb3e643dcb3ec0bdd694bb36cd8fad289ec"
}

variable "health_check_grace_period_seconds" {
  description = "Seconds ECS ignores failing load balancer health checks on a new task. Ignored without a target group."
  type        = number
  default     = 120

  validation {
    condition     = var.health_check_grace_period_seconds >= 0 && var.health_check_grace_period_seconds <= 3600 && floor(var.health_check_grace_period_seconds) == var.health_check_grace_period_seconds
    error_message = "health_check_grace_period_seconds must be a whole number of seconds between 0 and 3600."
  }
}

variable "enable_execute_command" {
  description = "Enable ECS Exec (aws ecs execute-command). The task role must allow ssmmessages:*Channel."
  type        = bool
  default     = false
}

variable "readonly_root_filesystem" {
  description = "Mount the container root filesystem read-only (ECS.5). /tmp is always writable (mode 1777)."
  type        = bool
  default     = true
}
