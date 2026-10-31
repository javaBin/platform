variable "name" {
  description = "Bucket purpose (e.g. 'data', 'uploads'). Bucket name: {team}-{service}-{name}-{account_id}"
  type        = string
}

variable "team" {
  description = "Team name, used as resource name prefix for team-scoped isolation"
  type        = string
}

variable "aws_account_id" {
  description = "AWS account ID (for globally unique bucket name)"
  type        = string
}

variable "service" {
  description = "App name (e.g. 'moresleep'), part of the bucket name and the service tag"
  type        = string
}

variable "versioning" {
  description = "Enable S3 versioning"
  type        = bool
  default     = true
}

variable "expire_days" {
  description = "Auto-expire objects after N days (0 = no expiration)"
  type        = number
  default     = 0
}
