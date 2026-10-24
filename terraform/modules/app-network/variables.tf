variable "name" {
  description = "App name"
  type        = string
}

variable "team" {
  description = "Owning team"
  type        = string
}

variable "project" {
  description = "Platform project name (resource and parameter prefix)"
  type        = string
  default     = "javabin"
}

variable "vpc_id" {
  description = "Platform VPC ID"
  type        = string
}

variable "port" {
  description = "Container port; the ALB and peers reach the app on it"
  type        = number
  default     = 8000
}

variable "alb_ingress" {
  description = "Allow the platform ALB to reach the app port (the app has routing)"
  type        = bool
  default     = false
}

variable "calls" {
  description = "Apps this app calls: \"team/app\" => scopes requested (empty list for network-only)"
  type        = map(list(string))
  default     = {}
}

variable "provides_scopes" {
  description = "Scopes this app offers to callers: scope => description"
  type        = map(string)
  default     = {}
}

variable "consumers" {
  description = "Apps allowed to call this app: \"team/app\" => scopes allowed (empty list for network-only)"
  type        = map(list(string))
  default     = {}
}

variable "internal_pool_name" {
  description = "Name of the internal Cognito user pool (M2M tokens are issued there)"
  type        = string
  default     = "javabin-internal"
}

variable "internal_pool_domain" {
  description = "Cognito hosted-domain prefix of the internal pool (its OAuth2 token endpoint). Today the same as the pool name; change both here if the domain ever changes"
  type        = string
  default     = "javabin-internal"
}
