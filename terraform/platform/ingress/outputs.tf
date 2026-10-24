output "alb_arn" {
  description = "ARN of the Application Load Balancer"
  value       = aws_lb.main.arn
}

output "alb_dns_name" {
  description = "DNS name of the Application Load Balancer"
  value       = aws_lb.main.dns_name
}

output "alb_zone_id" {
  description = "Zone ID of the Application Load Balancer (for Route53 alias records)"
  value       = aws_lb.main.zone_id
}

output "https_listener_arn" {
  description = "ARN of the HTTPS listener"
  value       = aws_lb_listener.https.arn
}

output "route53_zone_id" {
  description = "Zone ID of the Route53 hosted zone"
  value       = data.aws_route53_zone.main.zone_id
}

output "http_listener_arn" {
  description = "ARN of the port-80 HTTP-to-HTTPS redirect listener"
  value       = aws_lb_listener.http_redirect.arn
}
