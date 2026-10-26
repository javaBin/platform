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

variable "project" {
  description = "Project name for SSM parameter paths (sign-in clients)"
  type        = string
  default     = "javabin"
}

variable "login_rules" {
  description = <<-EOT
    ALB sign-in rules from app.yaml routing.login, keyed p1, p2, ... Each rule
    matches host + paths at its own priority (below listener_rule_priority).
    Protected rules require sign-in to the pool and membership of one of
    groups (empty = any pool user); public rules forward without sign-in.
  EOT
  type = map(object({
    priority = number
    paths    = list(string)
    public   = optional(bool, false)
    pool     = optional(string, "internal")
    groups   = optional(list(string), [])
  }))
  default = {}

  validation {
    condition     = alltrue([for k, v in var.login_rules : can(regex("^p[1-9][0-9]?$", k))])
    error_message = "login_rules keys must be p1, p2, ... (p1-p99)."
  }

  validation {
    condition     = alltrue([for v in values(var.login_rules) : v.priority >= 1 && v.priority <= 50000])
    error_message = "login_rules priorities must be between 1 and 50000."
  }

  validation {
    condition     = alltrue([for v in values(var.login_rules) : contains(["internal", "external"], v.pool)])
    error_message = "login_rules pool must be 'internal' or 'external'."
  }

  validation {
    condition     = alltrue([for v in values(var.login_rules) : length(v.paths) >= 1 && length(v.paths) <= 4])
    error_message = "login_rules need 1-4 paths each (an ALB rule takes 5 condition values; the host is one)."
  }
}

variable "login_session_timeout" {
  description = "Seconds an ALB sign-in session lasts. Bounds how long a removed group membership keeps working."
  type        = number
  default     = 28800
}

variable "login_client_wait" {
  description = "How long to wait for app-auth-sync to create a sign-in client before reading its credentials"
  type        = string
  default     = "60s"
}

variable "allow_dns_overwrite" {
  description = "Take over an existing A record for host_header (Route53 UPSERT) instead of failing because it exists"
  type        = bool
  default     = false
}
