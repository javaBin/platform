variable "app_name" {
  description = "Application name (e.g. 'moresleep')"
  type        = string
}

variable "team" {
  description = "Team that owns the app — used as a prefix on the Cognito client name to avoid cross-team collisions"
  type        = string
}

variable "pool" {
  description = "Which Cognito pool(s) to register with: 'internal', 'external', or 'both'"
  type        = string

  validation {
    condition     = contains(["internal", "external", "both"], var.pool)
    error_message = "Pool must be 'internal', 'external', or 'both'."
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
  description = "Custom domain registered on the external pool (e.g. auth-external.javazone.no). Empty if no custom domain is configured — COGNITO_DOMAIN will be empty for external apps."
  type        = string
  default     = ""
}

variable "callback_urls" {
  description = "Allowed OAuth callback URLs"
  type        = list(string)
}

variable "logout_urls" {
  description = "Allowed logout URLs"
  type        = list(string)
  default     = []
}

variable "allowed_oauth_flows" {
  description = "Allowed OAuth flows"
  type        = list(string)
  default     = ["code"]
}

variable "allowed_oauth_scopes" {
  description = "Allowed OAuth scopes"
  type        = list(string)
  default     = ["openid", "email", "profile"]
}

variable "generate_secret" {
  description = "Generate a client secret (true for server-side apps, false for SPAs)"
  type        = bool
  default     = true
}

variable "project" {
  description = "Project name for SSM parameter path prefix"
  type        = string
  default     = "javabin"
}
