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

# 2 only once every internal-pool app client has a managed-login branding
# style (app-auth-sync creates them; check with
# aws cognito-idp describe-managed-login-branding-by-client), or that
# client's sign-in page breaks.
variable "internal_managed_login_version" {
  description = "Internal pool domain: 1 = classic hosted UI, 2 = managed login (forwards prompt=select_account to Google)"
  type        = number
  default     = 2

  validation {
    condition     = contains([1, 2], var.internal_managed_login_version)
    error_message = "internal_managed_login_version must be 1 or 2."
  }
}

# Empty disables the probe client. A list naming Google waits for the internal
# Google IdP (its OAuth client in SSM).
variable "internal_idp_probe_identity_providers" {
  description = "IdPs on the platform's internal-pool probe client (platform-idp-probe); [] removes it"
  type        = list(string)
  default     = ["Google"]

  validation {
    condition     = !contains(var.internal_idp_probe_identity_providers, "COGNITO")
    error_message = "The internal pool must not offer COGNITO (local sign-up)."
  }
}

variable "google_client_id" {
  description = "Google OAuth 2.0 client ID for the external pool's Google IdP (from GCP console)"
  type        = string
  default     = ""
}

variable "google_client_secret" {
  description = "Google OAuth 2.0 client secret for the external pool's Google IdP (from GCP console)"
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
#
# Google (OIDC, internal pool) is the exception auth-claims knows about: its
# users must also have email_verified=true (mapped from Google), and the
# identity module refuses "Google" here until its OAuth client is in SSM.
# Cutover from GoogleSAML: ["Google"] (docs/cognito-google-setup.md).
variable "internal_identity_providers" {
  description = "Identity providers on internal-pool app clients (GoogleSAML is created outside Terraform)"
  type        = list(string)
  default     = ["Google"]

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
