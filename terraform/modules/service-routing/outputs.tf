output "target_group_arn" {
  description = "Target group ARN (pass to ecs-service module)"
  value       = aws_lb_target_group.this.arn
}

output "target_group_arn_suffix" {
  description = "Target group ARN suffix (for CloudWatch ALB metrics)"
  value       = aws_lb_target_group.this.arn_suffix
}

output "dns_name" {
  description = "FQDN created in Route53"
  value       = aws_route53_record.this.fqdn
}
