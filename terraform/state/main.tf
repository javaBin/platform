################################################################################
# State module — S3 buckets + DynamoDB tables for Terraform state management
#
# These resources are created by scripts/bootstrap.sh and imported into this
# module. See docs/bootstrap-runbook.md for the full procedure.
################################################################################

locals {
  state_bucket_name   = "${var.project}-terraform-state-${var.aws_account_id}"
  infra_lock_table    = "${var.project}-terraform-infra-lock"
  app_lock_table      = "${var.project}-terraform-app-locks"
  plan_artifacts_name = "${var.project}-ci-plan-artifacts-${var.aws_account_id}"
}

################################################################################
# State bucket — stores all Terraform state files
################################################################################

resource "aws_s3_bucket" "state" {
  bucket = local.state_bucket_name

  # Prevent accidental deletion of the state bucket
  lifecycle {
    prevent_destroy = true
  }

  tags = {
    Name = local.state_bucket_name
  }
}

resource "aws_s3_bucket_versioning" "state" {
  bucket = aws_s3_bucket.state.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "state" {
  bucket = aws_s3_bucket.state.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "state" {
  bucket = aws_s3_bucket.state.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

################################################################################
# Infra lock table — used by terraform/platform/ and terraform/state/
################################################################################

resource "aws_dynamodb_table" "infra_lock" {
  name         = local.infra_lock_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Name = local.infra_lock_table
  }
}

################################################################################
# App lock table — used by app repos (one state key per app)
################################################################################

resource "aws_dynamodb_table" "app_locks" {
  name         = local.app_lock_table
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "LockID"

  attribute {
    name = "LockID"
    type = "S"
  }

  tags = {
    Name = local.app_lock_table
  }
}

################################################################################
# Plan artifacts bucket — CI uploads plan files, 24h expiry
################################################################################

resource "aws_s3_bucket" "plan_artifacts" {
  bucket = local.plan_artifacts_name

  tags = {
    Name = local.plan_artifacts_name
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "plan_artifacts" {
  bucket = aws_s3_bucket.plan_artifacts.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "plan_artifacts" {
  bucket = aws_s3_bucket.plan_artifacts.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_lifecycle_configuration" "plan_artifacts" {
  bucket = aws_s3_bucket.plan_artifacts.id

  rule {
    id     = "expire-plan-artifacts-24h"
    status = "Enabled"

    filter {}

    expiration {
      days = 1
    }
  }
}
