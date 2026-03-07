output "ecs_cluster_name" {
  description = "Name of the ECS cluster"
  value       = aws_ecs_cluster.main.name
}

output "ecs_cluster_arn" {
  description = "ARN of the ECS cluster"
  value       = aws_ecs_cluster.main.arn
}

output "ecr_repo_urls" {
  description = "Map of CI ECR repository URLs (key: short name, value: repository URL)"
  value = {
    for key, repo in aws_ecr_repository.ci : key => repo.repository_url
  }
}
