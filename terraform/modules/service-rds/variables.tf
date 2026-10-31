variable "name" {
  description = "Database purpose (e.g. 'main'). Identifier: {team}-{service}-{name}; database name: name with - replaced by _"
  type        = string
}

variable "service" {
  description = "App name (e.g. 'moresleep'), part of the instance identifier and the password path"
  type        = string
}

variable "team" {
  description = "Team name, used as resource name prefix for team-scoped isolation"
  type        = string
}

variable "project" {
  description = "Project name prefix (for SSM parameter path)"
  type        = string
}

variable "master_username" {
  description = "Master user name; the app connects as this user"
  type        = string
  default     = "postgres"
}

variable "engine_version" {
  description = "PostgreSQL engine version"
  type        = string
  default     = "16"
}

variable "instance_class" {
  description = "RDS instance class"
  type        = string
  default     = "db.t3.micro"
}

variable "allocated_storage" {
  description = "Storage in GB"
  type        = number
  default     = 20
}

variable "subnet_ids" {
  description = "Private subnet IDs for DB subnet group"
  type        = list(string)
}

variable "vpc_id" {
  description = "VPC ID for security group"
  type        = string
}

variable "allowed_security_group_ids" {
  description = "Security group IDs allowed to connect (e.g., ECS tasks SG)"
  type        = list(string)
}

variable "backup_retention_period" {
  description = "Backup retention in days"
  type        = number
  default     = 7
}

variable "multi_az" {
  description = "Enable Multi-AZ deployment"
  type        = bool
  default     = false
}

variable "deletion_protection" {
  description = "Enable deletion protection"
  type        = bool
  default     = true
}

variable "tags" {
  description = "Additional tags"
  type        = map(string)
  default     = {}
}
