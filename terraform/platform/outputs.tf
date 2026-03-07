################################################################################
# VPC
################################################################################

output "vpc_id" {
  description = "ID of the VPC"
  value       = module.networking.vpc_id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets"
  value       = module.networking.public_subnet_ids
}

output "private_subnet_ids" {
  description = "IDs of the private subnets"
  value       = module.networking.private_subnet_ids
}

output "ecs_tasks_sg_id" {
  description = "ID of the ECS tasks security group"
  value       = module.networking.ecs_tasks_sg_id
}

################################################################################
# ALB
################################################################################

output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer"
  value       = module.ingress.alb_dns_name
}

output "alb_arn" {
  description = "ARN of the Application Load Balancer"
  value       = module.ingress.alb_arn
}

output "alb_zone_id" {
  description = "Route53 zone ID of the ALB (for alias records)"
  value       = module.ingress.alb_zone_id
}

output "https_listener_arn" {
  description = "ARN of the HTTPS listener"
  value       = module.ingress.https_listener_arn
}

output "route53_zone_id" {
  description = "Route53 zone ID for javazone.no"
  value       = module.ingress.route53_zone_id
}

################################################################################
# ECS
################################################################################

output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = module.compute.ecs_cluster_name
}

output "ecs_cluster_arn" {
  description = "ARN of the ECS cluster"
  value       = module.compute.ecs_cluster_arn
}

################################################################################
# IAM
################################################################################

output "ecs_execution_role_arn" {
  description = "ARN of the ECS execution role"
  value       = module.iam.ecs_execution_role_arn
}

output "developer_boundary_arn" {
  description = "ARN of the javabin-developer-boundary IAM policy"
  value       = module.iam.developer_boundary_arn
}

################################################################################
# Monitoring
################################################################################

output "alerts_topic_arn" {
  description = "ARN of the alerts SNS topic"
  value       = module.monitoring.alerts_topic_arn
}

output "security_topic_arn" {
  description = "ARN of the security SNS topic"
  value       = module.monitoring.security_topic_arn
}
