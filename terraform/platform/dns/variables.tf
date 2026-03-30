variable "project" {
  description = "Project name for tagging"
  type        = string
}

variable "region" {
  description = "AWS region"
  type        = string
}

variable "sso_portal_url" {
  description = "IAM Identity Center portal URL to redirect aws.javabin.no to"
  type        = string
  default     = "https://javabin.awsapps.com/start/"
}
