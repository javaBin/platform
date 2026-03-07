variable "project" {
  description = "Project name used for resource naming"
  type        = string
}

variable "domain" {
  description = "Base domain (e.g. javazone.no)"
  type        = string
}

variable "google_workspace_domain" {
  description = "Google Workspace domain for Cognito IdP hosted-domain restriction"
  type        = string
  default     = "java.no"
}

variable "google_client_id" {
  description = "Google OAuth 2.0 client ID for Cognito IdP (from GCP console)"
  type        = string
  default     = ""
}

variable "google_client_secret" {
  description = "Google OAuth 2.0 client secret for Cognito IdP (from GCP console)"
  type        = string
  sensitive   = true
  default     = ""
}

variable "enable_cognito" {
  description = "Whether to create Cognito user pools. Requires google_client_id and google_client_secret."
  type        = bool
  default     = false
}

variable "route53_zone_id" {
  description = "Route53 hosted zone ID for auth domain DNS records"
  type        = string
  default     = ""
}

variable "certificate_arn" {
  description = "ACM certificate ARN in us-east-1 for custom Cognito domains (CloudFront requirement)"
  type        = string
  default     = ""
}
