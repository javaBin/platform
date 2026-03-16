variable "region" {
  description = "AWS region for all resources"
  type        = string
  default     = "eu-central-1"
}

variable "environment" {
  description = "Environment name (e.g. production, staging)"
  type        = string
  default     = "production"
}

variable "project" {
  description = "Project name used for resource naming and tagging"
  type        = string
  default     = "javabin"
}

variable "aws_account_id" {
  description = "AWS account ID"
  type        = string
  default     = "553637109631"
}

variable "domain" {
  description = "Base domain for DNS (Route53 hosted zone)"
  type        = string
  default     = "javazone.no"
}

variable "registered_teams" {
  description = "GitHub team slugs that get CI team + deploy roles (synced from GitHub teams API)"
  type        = list(string)
  default     = []
}

variable "github_org" {
  description = "GitHub organization name for OIDC trust policies"
  type        = string
  default     = "javaBin"
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

variable "certificate_arn" {
  description = "ACM certificate ARN in us-east-1 for Cognito custom domains (CloudFront requirement)"
  type        = string
  default     = ""
}

variable "identity_store_id" {
  description = "IAM Identity Center identity store ID (from terraform/org/ outputs)"
  type        = string
  default     = "d-9967444724"
}

variable "sso_instance_arn" {
  description = "IAM Identity Center instance ARN (from terraform/org/)"
  type        = string
  default     = "arn:aws:sso:::instance/ssoins-6987654c95c9a069"
}

variable "auto_tagger_identities" {
  description = "IAM identity substrings allowed to trigger auto-tagging"
  type        = list(string)
  default = [
    "javabin-ci-infra",
    "javabin-ci-team-",
    "javabin-ci-deploy-",
    "javabin-",
    "alexander.amiri"
  ]
}
