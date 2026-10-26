variable "identifier" {
  description = "DB instance identifier of the existing RDS instance to adopt"
  type        = string

  validation {
    condition     = can(regex("^[a-zA-Z][a-zA-Z0-9-]{0,62}$", var.identifier))
    error_message = "identifier must be a valid RDS DB instance identifier."
  }
}

variable "deletion_protection" {
  description = "Enable RDS deletion protection on the adopted instance"
  type        = bool
  default     = true
}

variable "backup_retention_period" {
  description = "Automated backup retention in days. null keeps the instance's current value."
  type        = number
  default     = null
}

variable "tags" {
  description = "Additional tags (provider default_tags are applied as well)"
  type        = map(string)
  default     = {}
}
