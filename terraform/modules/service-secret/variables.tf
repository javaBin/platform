variable "name" {
  description = "Secret purpose (e.g. 'slack-token'). Parameter name: /{project}/apps/{team}/{service}/{name}"
  type        = string
}

variable "team" {
  description = "Team name, used as resource name prefix for team-scoped isolation"
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
