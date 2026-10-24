variable "project" {
  description = "Platform project name"
  type        = string
  default     = "javabin"
}

variable "region" {
  description = "AWS region"
  type        = string
  default     = "eu-central-1"
}

variable "domain" {
  description = "Base domain (e.g. javazone.no)"
  type        = string
  default     = "javazone.no"
}

variable "team" {
  description = "Owning team — selects the team's ECS execution role"
  type        = string
}
