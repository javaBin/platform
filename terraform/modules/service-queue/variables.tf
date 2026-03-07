variable "name" {
  description = "Queue purpose (e.g. 'tasks', 'events'). Queue name: {project}-{name}"
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
