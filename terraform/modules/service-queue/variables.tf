variable "name" {
  description = "Queue purpose (e.g. 'tasks', 'events'). Queue name: {team}-{service}-{name}"
  type        = string
}

variable "team" {
  description = "Team name, used as resource name prefix for team-scoped isolation"
  type        = string
}

variable "service" {
  description = "App name (e.g. 'moresleep'), part of the resource name and the service tag"
  type        = string
}

variable "visibility_timeout_seconds" {
  description = "How long a message is hidden after being received (default: 30s)"
  type        = number
  default     = 30
}

variable "retention_seconds" {
  description = "How long messages are kept in the queue (default: 4 days)"
  type        = number
  default     = 345600
}

variable "max_receive_count" {
  description = "Number of receive attempts before sending to DLQ (default: 3)"
  type        = number
  default     = 3
}

variable "dlq_retention_seconds" {
  description = "How long messages are kept in the DLQ (default: 14 days)"
  type        = number
  default     = 1209600
}
