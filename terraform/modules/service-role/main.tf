################################################################################
# ECS Task IAM Role
################################################################################

resource "aws_iam_role" "this" {
  name = "${var.project}-${var.name}"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Service = "ecs-tasks.amazonaws.com"
      }
      Action = "sts:AssumeRole"
    }]
  })

  tags = {
    Name = "${var.project}-${var.name}"
  }
}

################################################################################
# CloudWatch Logs (every ECS task needs this)
################################################################################

resource "aws_iam_role_policy" "logs" {
  name = "cloudwatch-logs"
  role = aws_iam_role.this.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = [
        "logs:CreateLogStream",
        "logs:PutLogEvents"
      ]
      Resource = "arn:aws:logs:${var.region}:${var.aws_account_id}:log-group:/ecs/${var.name}:*"
    }]
  })
}

################################################################################
# Additional Policies (from service-bucket, service-database, etc.)
################################################################################

resource "aws_iam_role_policy" "additional" {
  for_each = var.additional_policy_jsons
  name     = each.key
  role     = aws_iam_role.this.id

  policy = each.value
}
