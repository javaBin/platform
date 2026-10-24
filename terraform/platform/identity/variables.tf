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

variable "org_boundary_arn" {
  description = "Permissions boundary for roles created here (required by the org boundary)"
  type        = string
}

# Sign-in IdPs per pool. app-auth-sync puts exactly this list on every app
# client; with one IdP the hosted UI redirects straight to it. "COGNITO" means
# local username/password users. Every other name must exist in the pool, or
# app-auth-sync skips the request (and logs an error) instead of changing it.
#
# Internal pool: auth-claims also trusts every IdP in this list to vouch for the
# user's email (no email_verified check) and looks up registry groups by it. Only
# list IdPs that release java.no Workspace accounts and nothing else: GoogleSAML
# does. A Google OIDC IdP qualifies only if its GCP OAuth client is Internal
# (Workspace-only); otherwise hd= is just a hint and a consumer Google account
# with an @java.no address would pass. In that case map email_verified/hd and
# check them in auth-claims before adding the IdP here.
variable "internal_identity_providers" {
  description = "Identity providers on internal-pool app clients (GoogleSAML is created outside Terraform)"
  type        = list(string)
  default     = ["GoogleSAML"]

  validation {
    condition     = length(var.internal_identity_providers) > 0 && !contains(var.internal_identity_providers, "COGNITO")
    error_message = "The internal pool needs at least one federated IdP and must not offer COGNITO (local sign-up)."
  }
}

variable "external_identity_providers" {
  description = "Identity providers on external-pool app clients"
  type        = list(string)
  default     = ["COGNITO"]

  validation {
    condition     = length(var.external_identity_providers) > 0
    error_message = "The external pool needs at least one identity provider."
  }
}
