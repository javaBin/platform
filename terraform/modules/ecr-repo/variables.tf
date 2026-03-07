variable "name" {
  description = "Repository name (e.g. 'moresleep')"
  type        = string
}

variable "scan_on_push" {
  description = "Enable image scanning on push"
  type        = bool
  default     = true
}

variable "semver_keep_count" {
  description = "Number of semver-tagged images to keep (v* prefix)"
  type        = number
  default     = 5
}

variable "main_keep_count" {
  description = "Number of main/master branch build images to keep"
  type        = number
  default     = 3
}

variable "untagged_expire_days" {
  description = "Days to keep untagged images before expiry"
  type        = number
  default     = 1
}

variable "other_expire_days" {
  description = "Days to keep other tagged images before expiry"
  type        = number
  default     = 7
}
