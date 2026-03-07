variable "name" {
  description = "Bucket purpose (e.g. 'data', 'uploads'). Bucket name: {project}-{name}-{account_id}"
  type        = string
}

variable "project" {
  description = "Project name prefix"
  type        = string
}

variable "aws_account_id" {
  description = "AWS account ID (for globally unique bucket name)"
  type        = string
}

variable "service" {
  description = "Service tag value (e.g. 'moresleep')"
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
