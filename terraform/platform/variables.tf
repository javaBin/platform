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

variable "registered_app_repos" {
  description = "GitHub repo names (under javaBin/) registered for CI/CD access"
  type        = list(string)
  default     = ["platform-test-app"]
}

variable "override_approvers" {
  description = "GitHub usernames permitted to approve risk gate overrides"
  type        = list(string)
  default     = ["alexander-amiri"]
  # Add board members and infra leads here.
  # Changing this list requires a PR to platform reviewed by platform-owners.
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

variable "auto_tagger_identities" {
  description = "IAM identity substrings allowed to trigger auto-tagging"
  type        = list(string)
  default = [
    "javabin-ci-infra",
    "javabin-ci-app-",
    "javabin-ci-deploy-",
    "javabin-",
    "alexander.amiri"
  ]
}
