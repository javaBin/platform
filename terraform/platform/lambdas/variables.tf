variable "project" {
  description = "Project name used for resource naming"
  type        = string
}

variable "region" {
  description = "AWS region"
  type        = string
}

variable "aws_account_id" {
  description = "AWS account ID"
  type        = string
}

variable "alerts_topic_arn" {
  description = "ARN of the javabin-alerts SNS topic (cost anomalies, budgets)"
  type        = string
}

variable "security_topic_arn" {
  description = "ARN of the javabin-security SNS topic (IAM, GuardDuty, Config, Security Hub)"
  type        = string
}

variable "compliance_reporter_identities" {
  description = "IAM identity substrings allowed to trigger compliance reporting"
  type        = list(string)
}

variable "internal_user_pool_id" {
  description = "Cognito internal user pool ID"
  type        = string
}

variable "internal_user_pool_arn" {
  description = "Cognito internal user pool ARN"
  type        = string
}

variable "external_user_pool_arn" {
  description = "Cognito external user pool ARN (app-auth-sync registers clients here)"
  type        = string
}

variable "identity_store_id" {
  description = "IAM Identity Center identity store ID"
  type        = string
  default     = ""
}

variable "sso_instance_arn" {
  description = "IAM Identity Center instance ARN"
  type        = string
  default     = ""
}

variable "alert_dedup_table_name" {
  description = "DynamoDB table name for alert deduplication"
  type        = string
}

variable "alert_dedup_table_arn" {
  description = "DynamoDB table ARN for alert deduplication"
  type        = string
}

variable "https_listener_arn" {
  description = "ARN of the ALB HTTPS listener for adding routing rules"
  type        = string
}

variable "alb_dns_name" {
  description = "DNS name of the ALB for Route53 alias records"
  type        = string
}

variable "alb_zone_id" {
  description = "Hosted zone ID of the ALB for Route53 alias records"
  type        = string
}

variable "route53_zone_id" {
  description = "Route53 hosted zone ID for javazone.no"
  type        = string
}

variable "domain" {
  description = "Domain name (e.g. javazone.no)"
  type        = string
}

variable "org_boundary_arn" {
  description = "ARN of the org permission boundary (for cross-team Lambda roles)"
  type        = string
}

variable "developer_boundary_arn" {
  description = "ARN of the developer permission boundary (for team-scoped Lambda roles)"
  type        = string
}

# --- Cost analytics (CUR / Athena) ---

variable "cur_glue_database" {
  description = "Glue catalog database name for CUR data"
  type        = string
}

variable "cur_glue_table" {
  description = "Glue catalog table name for CUR data"
  type        = string
}

variable "athena_workgroup" {
  description = "Athena workgroup name for cost queries"
  type        = string
}

variable "athena_results_bucket_arn" {
  description = "ARN of the S3 bucket for Athena query results"
  type        = string
}

variable "cur_data_bucket_arn" {
  description = "ARN of the S3 bucket containing CUR Parquet data"
  type        = string
}


variable "auth_memberships_table_name" {
  description = "Auth memberships table (registry groups + per-client allowed groups)"
  type        = string
}

variable "auth_memberships_table_arn" {
  description = "ARN of the auth memberships table"
  type        = string
}

variable "internal_identity_providers" {
  description = "Sign-in IdPs app-auth-sync puts on internal-pool app clients"
  type        = list(string)
}

variable "external_identity_providers" {
  description = "Sign-in IdPs app-auth-sync puts on external-pool app clients"
  type        = list(string)
}

variable "atlassian_sync_apply" {
  description = "Let team-provisioner write Atlassian group memberships; false = report the plan only"
  type        = bool
  default     = false
}
