variable "project" {
  description = "Project name for tagging"
  type        = string
}

variable "region" {
  description = "AWS region"
  type        = string
}

variable "javazone_zone_id" {
  description = "Route53 zone ID for javazone.no (managed outside this module) — hosts aws.javazone.no"
  type        = string
}

variable "sso_portal_url" {
  description = "IAM Identity Center portal URL to redirect aws.javabin.no, aws.java.no and aws.javazone.no to"
  type        = string
  default     = "https://javabin.awsapps.com/start/"
}
