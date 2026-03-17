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

################################################################################
# Master password — generated once, stored in SSM
################################################################################

resource "random_password" "master" {
  length           = 32
  special          = true
  override_special = "!#$%&*()-_=+[]{}<>:?"
}

resource "aws_ssm_parameter" "master_password" {
  name        = "/${var.project}/apps/${var.name}/db-master-password"
  description = "RDS master password for ${var.name}"
  type        = "SecureString"
  value       = random_password.master.result

  tags = merge(var.tags, {
    Name = "${var.name}-db-master-password"
  })

  lifecycle {
    ignore_changes = [value]
  }
}

################################################################################
# RDS Instance
################################################################################

resource "aws_db_instance" "this" {
  identifier     = var.name
  engine         = "postgres"
  engine_version = var.engine_version
  instance_class = var.instance_class

  allocated_storage = var.allocated_storage
  storage_encrypted = true

  db_name  = replace(var.name, "-", "_")
  username = "postgres"
  password = random_password.master.result

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

  lifecycle {
    ignore_changes = [password]
  }
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
    sid    = "SSMReadDBPassword"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
    ]
    resources = [
      aws_ssm_parameter.master_password.arn,
    ]
  }
}
