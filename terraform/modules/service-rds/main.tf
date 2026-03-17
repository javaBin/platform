################################################################################
# RDS PostgreSQL — managed database for service data
################################################################################

resource "aws_db_subnet_group" "this" {
  name       = "${var.name}-db-subnet"
  subnet_ids = var.subnet_ids

  tags = merge(var.tags, {
    Name = "${var.name}-db-subnet"
  })
}

resource "aws_security_group" "this" {
  name        = "${var.name}-rds-sg"
  description = "Allow PostgreSQL access from ECS tasks"
  vpc_id      = var.vpc_id

  tags = merge(var.tags, {
    Name = "${var.name}-rds-sg"
  })
}

resource "aws_security_group_rule" "this" {
  count                    = length(var.allowed_security_group_ids)
  type                     = "ingress"
  from_port                = 5432
  to_port                  = 5432
  protocol                 = "tcp"
  source_security_group_id = var.allowed_security_group_ids[count.index]
  security_group_id        = aws_security_group.this.id
}

resource "aws_db_instance" "this" {
  identifier     = var.name
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage = var.allocated_storage
  storage_encrypted = true

  db_name  = replace(var.name, "-", "_")
  username = "postgres"

  manage_master_user_password = true

  db_subnet_group_name   = aws_db_subnet_group.this.name
  vpc_security_group_ids = [aws_security_group.this.id]

  backup_retention_period = var.backup_retention_period
  multi_az                = var.multi_az
  deletion_protection     = var.deletion_protection

  skip_final_snapshot       = false
  final_snapshot_identifier = "${var.name}-final"

  tags = merge(var.tags, {
    Name = var.name
  })
}

################################################################################
# IAM Policy Document — for attaching to task roles
################################################################################

data "aws_iam_policy_document" "access" {
  statement {
    sid    = "RDSConnect"
    effect = "Allow"
    actions = [
      "rds-db:connect",
    ]
    resources = [
      # ARN format: arn:aws:rds-db:{region}:{account}:dbuser:{dbi-resource-id}/*
      "arn:aws:rds-db:*:*:dbuser:${aws_db_instance.this.resource_id}/*",
    ]
  }

  statement {
    sid    = "SecretsManagerRead"
    effect = "Allow"
    actions = [
      "secretsmanager:GetSecretValue",
      "secretsmanager:DescribeSecret",
    ]
    resources = [
      aws_db_instance.this.master_user_secret[0].secret_arn,
    ]
  }
}
