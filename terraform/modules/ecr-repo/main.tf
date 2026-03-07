################################################################################
# ECR Repository with Lifecycle Policy
################################################################################

resource "aws_ecr_repository" "this" {
  name                 = var.name
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {
    scan_on_push = var.scan_on_push
  }

  encryption_configuration {
    encryption_type = "AES256"
  }

  tags = {
    Name = var.name
  }
}

resource "aws_ecr_lifecycle_policy" "this" {
  repository = aws_ecr_repository.this.name

  policy = jsonencode({
    rules = [
      {
        rulePriority = 1
        description  = "Keep last ${var.semver_keep_count} semver releases"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["v"]
          countType     = "imageCountMoreThan"
          countNumber   = var.semver_keep_count
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 2
        description  = "Keep last ${var.main_keep_count} main builds"
        selection = {
          tagStatus     = "tagged"
          tagPrefixList = ["main-", "master-"]
          countType     = "imageCountMoreThan"
          countNumber   = var.main_keep_count
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 3
        description  = "Expire untagged images after ${var.untagged_expire_days} day(s)"
        selection = {
          tagStatus   = "untagged"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = var.untagged_expire_days
        }
        action = { type = "expire" }
      },
      {
        rulePriority = 4
        description  = "Expire other tags after ${var.other_expire_days} days"
        selection = {
          tagStatus   = "any"
          countType   = "sinceImagePushed"
          countUnit   = "days"
          countNumber = var.other_expire_days
        }
        action = { type = "expire" }
      }
    ]
  })
}
