variable "name" {
  description = "Service name (e.g. 'moresleep'). Target group name: {project}-{name}"
  type        = string
}

variable "project" {
  description = "Platform project name (e.g. 'javabin')"
  type        = string
}

variable "vpc_id" {
  description = "VPC ID for the target group"
  type        = string
}

variable "port" {
  description = "Container port the service listens on"
  type        = number
  default     = 8000
}

variable "health_check_path" {
  description = "HTTP path for health checks"
  type        = string
  default     = "/health"
}

variable "health_check_matcher" {
  description = "HTTP status codes to consider healthy"
  type        = string
  default     = "200"
}

variable "https_listener_arn" {
  description = "ARN of the HTTPS listener to attach the rule to"
  type        = string
}

variable "listener_rule_priority" {
  description = "Priority for the listener rule (must be unique across all apps, 1-50000)"
  type        = number

  validation {
    condition     = var.listener_rule_priority >= 1 && var.listener_rule_priority <= 50000
    error_message = "Listener rule priority must be between 1 and 50000."
  }
}

variable "host_header" {
  description = "Host header value for routing (e.g. 'moresleep.javazone.no')"
  type        = string
}

variable "route53_zone_id" {
  description = "Route53 hosted zone ID for creating the DNS record"
  type        = string
}

variable "alb_dns_name" {
  description = "ALB DNS name for the Route53 alias target"
  type        = string
}

variable "alb_zone_id" {
  description = "ALB hosted zone ID for the Route53 alias target"
  type        = string
}

variable "deregistration_delay" {
  description = "Seconds to wait for in-flight requests before deregistering targets"
  type        = number
  default     = 30
}
