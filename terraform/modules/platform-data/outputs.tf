output "vpc_id" {
  description = "VPC ID"
  value       = data.aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "Private subnet IDs for ECS tasks"
  value       = data.aws_subnets.private.ids
}

output "public_subnet_ids" {
  description = "Public subnet IDs"
  value       = data.aws_subnets.public.ids
}

output "ecs_tasks_security_group_id" {
  description = "Security group ID for ECS tasks (allows ALB inbound)"
  value       = data.aws_security_group.ecs_tasks.id
}

output "alb_arn" {
  description = "ALB ARN"
  value       = data.aws_lb.main.arn
}

output "alb_arn_suffix" {
  description = "ALB ARN suffix (for CloudWatch ALB metrics)"
  value       = data.aws_lb.main.arn_suffix
}

output "alb_dns_name" {
  description = "ALB DNS name (for Route53 alias records)"
  value       = data.aws_lb.main.dns_name
}

output "alb_zone_id" {
  description = "ALB hosted zone ID (for Route53 alias records)"
  value       = data.aws_lb.main.zone_id
}

output "https_listener_arn" {
  description = "HTTPS listener ARN (for adding listener rules)"
  value       = data.aws_lb_listener.https.arn
}

output "ecs_cluster_id" {
  description = "ECS cluster ID"
  value       = data.aws_ecs_cluster.main.id
}

output "ecs_cluster_name" {
  description = "ECS cluster name"
  value       = data.aws_ecs_cluster.main.cluster_name
}

output "execution_role_arn" {
  description = "The team's ECS execution role ARN (reads only that team's secrets)"
  value       = data.aws_iam_role.ecs_execution.arn
}

output "route53_zone_id" {
  description = "Route53 hosted zone ID for the domain"
  value       = data.aws_route53_zone.main.zone_id
}

output "route53_zone_ids" {
  description = "Route53 hosted zone ID per app domain (e.g. route53_zone_ids[\"java.no\"])"
  value       = { for d, z in data.aws_route53_zone.app : d => z.zone_id }
}

output "domain" {
  description = "Base domain"
  value       = var.domain
}

output "region" {
  description = "AWS region"
  value       = var.region
}

output "project" {
  description = "Platform project name"
  value       = var.project
}

output "developer_boundary_arn" {
  description = "Permission boundary ARN for service roles"
  value       = "arn:aws:iam::${data.aws_caller_identity.current.account_id}:policy/${var.project}-developer-boundary"
}
