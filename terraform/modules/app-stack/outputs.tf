################################################################################
# Container Image
################################################################################

output "ecr_url" {
  description = "ECR repository URL"
  value       = local.ecr_url
}

output "ecr_name" {
  description = "ECR repository name"
  value       = local.ecr_name
}

################################################################################
# ECS
################################################################################

output "service_name" {
  description = "ECS service name"
  value       = module.service.service_name
}

output "task_definition_arn" {
  description = "ECS task definition ARN"
  value       = module.service.task_definition_arn
}

output "log_group_name" {
  description = "CloudWatch log group name"
  value       = module.service.log_group_name
}

################################################################################
# IAM
################################################################################

output "task_role_arn" {
  description = "ECS task IAM role ARN"
  value       = module.task_role.role_arn
}

output "task_role_id" {
  description = "ECS task IAM role ID (for attaching additional inline policies)"
  value       = module.task_role.role_id
}

output "task_role_name" {
  description = "ECS task IAM role name"
  value       = module.task_role.role_name
}

################################################################################
# Networking
################################################################################

output "dns_name" {
  description = "FQDN created in Route53"
  value       = module.routing.dns_name
}

output "target_group_arn" {
  description = "ALB target group ARN"
  value       = module.routing.target_group_arn
}

################################################################################
# Resources (dynamic maps keyed by YAML name)
################################################################################

output "bucket_names" {
  description = "Map of YAML bucket name => S3 bucket name"
  value       = { for name, mod in module.buckets : name => mod.bucket_name }
}

output "bucket_arns" {
  description = "Map of YAML bucket name => S3 bucket ARN"
  value       = { for name, mod in module.buckets : name => mod.bucket_arn }
}

output "table_names" {
  description = "Map of YAML database name => DynamoDB table name"
  value       = { for name, mod in module.databases : name => mod.table_name }
}

output "table_arns" {
  description = "Map of YAML database name => DynamoDB table ARN"
  value       = { for name, mod in module.databases : name => mod.table_arn }
}

output "secret_arns" {
  description = "Map of YAML secret name => Secrets Manager secret ARN"
  value       = { for name, mod in module.secrets : name => mod.secret_arn }
}

output "queue_urls" {
  description = "Map of YAML queue name => SQS queue URL"
  value       = { for name, mod in module.queues : name => mod.queue_url }
}

output "queue_arns" {
  description = "Map of YAML queue name => SQS queue ARN"
  value       = { for name, mod in module.queues : name => mod.queue_arn }
}

output "dlq_urls" {
  description = "Map of YAML queue name => SQS dead-letter queue URL"
  value       = { for name, mod in module.queues : name => mod.dlq_url }
}

################################################################################
# Platform (passthrough for custom Terraform)
################################################################################

output "platform" {
  description = "All shared infra values (vpc_id, subnet_ids, etc.) for custom Terraform"
  value = {
    vpc_id                      = module.platform.vpc_id
    private_subnet_ids          = module.platform.private_subnet_ids
    public_subnet_ids           = module.platform.public_subnet_ids
    ecs_cluster_id              = module.platform.ecs_cluster_id
    ecs_cluster_name            = module.platform.ecs_cluster_name
    ecs_tasks_security_group_id = module.platform.ecs_tasks_security_group_id
    execution_role_arn          = module.platform.execution_role_arn
    https_listener_arn          = module.platform.https_listener_arn
    route53_zone_id             = module.platform.route53_zone_id
    alb_arn                     = module.platform.alb_arn
    alb_dns_name                = module.platform.alb_dns_name
    alb_zone_id                 = module.platform.alb_zone_id
    region                      = module.platform.region
    project                     = module.platform.project
  }
}

################################################################################
# App config
################################################################################

output "app_name" {
  description = "Application name from YAML (base name without environment suffix)"
  value       = local.name
}

output "environment_name" {
  description = "Environment name (empty string for single-environment apps)"
  value       = var.environment_name
}

output "qualified_name" {
  description = "Environment-qualified name used for resource naming (e.g. 'moresleep-dev')"
  value       = local.qualified_name
}
