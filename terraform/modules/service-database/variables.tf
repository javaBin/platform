variable "name" {
  description = "Table purpose (e.g. 'sessions', 'cache'). Table name: {project}-{name}"
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

variable "hash_key" {
  description = "Partition key attribute name"
  type        = string
  default     = "id"
}

variable "hash_key_type" {
  description = "Partition key type (S, N, B)"
  type        = string
  default     = "S"
}

variable "range_key" {
  description = "Sort key attribute name (null = no sort key)"
  type        = string
  default     = null
}

variable "range_key_type" {
  description = "Sort key type (S, N, B)"
  type        = string
  default     = "S"
}

variable "billing_mode" {
  description = "DynamoDB billing mode (PAY_PER_REQUEST or PROVISIONED)"
  type        = string
  default     = "PAY_PER_REQUEST"
}

variable "point_in_time_recovery" {
  description = "Enable point-in-time recovery"
  type        = bool
  default     = true
}

variable "ttl_attribute" {
  description = "TTL attribute name (empty string = disabled)"
  type        = string
  default     = ""
}
