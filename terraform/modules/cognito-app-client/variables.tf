variable "app_name" {
  description = "Application name (e.g. 'moresleep')"
  type        = string
}

variable "team" {
  description = "Owning team — the client is named <team>-<app_name>"
  type        = string
}

variable "slot" {
  description = "Optional extra client for the same app (e.g. 'login-1' for ALB sign-in rules). Empty = the app's main client."
  type        = string
  default     = ""

  validation {
    condition     = can(regex("^$|^login-[1-9][0-9]?$", var.slot))
    error_message = "slot must be empty or login-<1-99>."
  }
}

variable "pool" {
  description = "Which Cognito pool to register with: 'internal' or 'external'"
  type        = string

  validation {
    condition     = contains(["internal", "external"], var.pool)
    error_message = "Pool must be 'internal' or 'external'."
  }
}

variable "internal_pool_name" {
  description = "Name of the internal Cognito user pool (looked up by name)"
  type        = string
  default     = "javabin-internal"
}

variable "external_pool_name" {
  description = "Name of the external Cognito user pool (looked up by name)"
  type        = string
  default     = "javabin-external"
}

variable "external_pool_custom_domain" {
  description = "Custom domain registered on the external pool (e.g. auth-external.javazone.no). Empty = the pool's Cognito prefix domain (<external_pool_name>.auth.<region>.amazoncognito.com)."
  type        = string
  default     = ""
}

variable "client_type" {
  description = "confidential (server-side, with secret), public (browser/PKCE, no secret) or none (validate tokens only, no client)"
  type        = string
  default     = "confidential"

  validation {
    condition     = contains(["confidential", "public", "none"], var.client_type)
    error_message = "client_type must be confidential, public or none."
  }
}

variable "callback_urls" {
  description = "Allowed OAuth callback URLs (https, or http://localhost for local development)"
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for u in var.callback_urls : can(regex("^(https://|http://(localhost|127\\.0\\.0\\.1)(:[0-9]+)?(/|$))", u))])
    error_message = "callback_urls must be https:// URLs or http://localhost URLs."
  }
}

variable "logout_urls" {
  description = "Allowed logout URLs (https, or http://localhost for local development)"
  type        = list(string)
  default     = []

  validation {
    condition     = alltrue([for u in var.logout_urls : can(regex("^(https://|http://(localhost|127\\.0\\.0\\.1)(:[0-9]+)?(/|$))", u))])
    error_message = "logout_urls must be https:// URLs or http://localhost URLs."
  }
}

variable "allowed_groups" {
  description = "Only users in at least one of these groups can sign in to this client (empty = any pool user)"
  type        = list(string)
  default     = []
}

variable "allowed_oauth_scopes" {
  description = "Allowed OAuth scopes"
  type        = list(string)
  default     = ["openid", "email", "profile"]
}

variable "project" {
  description = "Project name for SSM parameter path prefix"
  type        = string
  default     = "javabin"
}
