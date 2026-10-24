variable "name" {
  description = "Service name (e.g. 'moresleep'). Target group name: {team}-{name}"
  type        = string
}

variable "team" {
  description = "Team name, used as resource name prefix for team-scoped isolation"
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
  description = "Host header value for routing (e.g. 'moresleep.javazone.no' or 'app.java.no')"
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

variable "allow_dns_overwrite" {
  description = "Take over an existing A record for host_header (Route53 UPSERT) instead of failing because it exists"
  type        = bool
  default     = false
}
