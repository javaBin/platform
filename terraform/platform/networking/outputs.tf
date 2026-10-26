output "vpc_id" {
  description = "ID of the VPC"
  value       = aws_vpc.main.id
}

output "public_subnet_ids" {
  description = "IDs of the public subnets"
  value       = [aws_subnet.public_a.id, aws_subnet.public_b.id]
}

output "private_subnet_ids" {
  description = "IDs of the private subnets"
  value       = [aws_subnet.private_a.id, aws_subnet.private_b.id]
}

output "alb_sg_id" {
  description = "Security group ID for the ALB"
  value       = aws_security_group.alb.id
}

output "ecs_tasks_sg_id" {
  description = "Security group ID for ECS tasks"
  value       = aws_security_group.ecs_tasks.id
}

output "vpc_arn" {
  description = "ARN of the VPC"
  value       = aws_vpc.main.arn
}

output "service_discovery_namespace_id" {
  description = "ID of the private app-to-app Cloud Map namespace"
  value       = aws_service_discovery_private_dns_namespace.internal.id
}

output "service_discovery_namespace_arn" {
  description = "ARN of the private app-to-app Cloud Map namespace"
  value       = aws_service_discovery_private_dns_namespace.internal.arn
}

output "service_discovery_namespace_name" {
  description = "Name of the private app-to-app Cloud Map namespace"
  value       = aws_service_discovery_private_dns_namespace.internal.name
}

output "service_discovery_hosted_zone_id" {
  description = "Route53 private hosted zone Cloud Map created for the namespace (protected in the developer boundary)"
  value       = aws_service_discovery_private_dns_namespace.internal.hosted_zone
}
