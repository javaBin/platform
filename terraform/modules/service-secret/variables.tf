variable "name" {
  description = "Secret purpose (e.g. 'slack-token'). Secret name: {project}/{name}"
  type        = string
}

variable "project" {
  description = "Project name prefix"
  type        = string
}

variable "service" {
  description = "Service tag value (e.g. 'moresleep')"
  type        = string
}

variable "description" {
  description = "Human-readable description of the secret"
  type        = string
  default     = ""
}
