variable "project" {
  description = "Project name used for resource naming"
  type        = string
}

variable "domain" {
  description = "Base domain for DNS (e.g. javazone.no)"
  type        = string
}

variable "public_subnet_ids" {
  description = "Public subnet IDs for ALB"
  type        = list(string)
}

variable "alb_sg_id" {
  description = "Security group ID for the ALB"
  type        = string
}
