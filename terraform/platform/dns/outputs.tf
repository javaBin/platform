output "java_no_zone_id" {
  description = "Route53 hosted zone ID for java.no"
  value       = aws_route53_zone.java_no.zone_id
}

output "java_no_nameservers" {
  description = "NS records to set at Domeneshop for java.no"
  value       = aws_route53_zone.java_no.name_servers
}

output "javabin_no_zone_id" {
  description = "Route53 hosted zone ID for javabin.no"
  value       = aws_route53_zone.javabin_no.zone_id
}

output "javabin_no_nameservers" {
  description = "NS records to set at Domeneshop for javabin.no"
  value       = aws_route53_zone.javabin_no.name_servers
}

output "javabin_com_zone_id" {
  description = "Route53 hosted zone ID for javabin.com"
  value       = aws_route53_zone.javabin_com.zone_id
}

output "javabin_com_nameservers" {
  description = "NS records to set at Domeneshop for javabin.com"
  value       = aws_route53_zone.javabin_com.name_servers
}

output "teknologihuset_no_zone_id" {
  description = "Route53 hosted zone ID for teknologihuset.no"
  value       = aws_route53_zone.teknologihuset_no.zone_id
}

output "teknologihuset_no_nameservers" {
  description = "NS records to set at Domeneshop for teknologihuset.no"
  value       = aws_route53_zone.teknologihuset_no.name_servers
}
