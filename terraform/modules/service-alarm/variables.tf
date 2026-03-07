variable "name" {
  description = "Service name (must match ECS service name)"
  type        = string
}

variable "project" {
  description = "Project name prefix"
  type        = string
}

variable "service" {
  description = "Service tag value (e.g. 'moresleep')"
  type        = string
}

variable "cluster_name" {
  description = "ECS cluster name"
  type        = string
}

variable "sns_topic_arns" {
  description = "SNS topic ARNs for alarm notifications"
  type        = list(string)
}

variable "cpu_threshold" {
  description = "CPU utilization alarm threshold (percent)"
  type        = number
  default     = 80
}

variable "memory_threshold" {
  description = "Memory utilization alarm threshold (percent)"
  type        = number
  default     = 80
}

variable "error_5xx_threshold" {
  description = "5xx error count threshold per 5-minute period"
  type        = number
  default     = 10
}

variable "enable_alb_alarms" {
  description = "Whether to create ALB-based alarms (unhealthy targets, 5xx). Must be a static value, not derived from resource attributes."
  type        = bool
  default     = true
}

variable "target_group_arn_suffix" {
  description = "Target group ARN suffix for ALB alarms"
  type        = string
  default     = ""
}

variable "alb_arn_suffix" {
  description = "ALB ARN suffix for ALB alarms"
  type        = string
  default     = ""
}
