################################################################################
# Cost Analytics — CUR, Athena, Glue, billing protection
#
# Resources:
#   S3 buckets          CUR data + Athena query results
#   CUR report          Daily Parquet export with resource IDs
#   Glue database       Catalog for CUR data
#   Glue crawler        Auto-discovers CUR Parquet schema daily
#   Athena workgroup    Scoped workgroup for cost queries
#   Account budget      Monthly budget: forecast >100%, actual >100%, actual >150%
#
# There is no CloudWatch EstimatedCharges alarm: the AWS/Billing metric is not
# enabled for this account (alarm sat in INSUFFICIENT_DATA from 2026-03 until it
# was removed). The budget's FORECASTED notification is the early warning.
################################################################################

terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws, aws.us_east_1]
    }
  }
}

locals {
  cur_bucket_name    = "${var.project}-cur-${var.aws_account_id}"
  athena_bucket_name = "${var.project}-athena-results-${var.aws_account_id}"
  glue_database_name = "${var.project}_cur"
  crawler_role_name  = "${var.project}-cur-crawler"
  athena_workgroup   = "${var.project}-cost-analytics"
  cur_report_name    = "${var.project}-cur"
  cur_s3_prefix      = "cur"
}

################################################################################
# S3 — CUR data bucket
################################################################################

resource "aws_s3_bucket" "cur_data" {
  bucket = local.cur_bucket_name
}

resource "aws_s3_bucket_public_access_block" "cur_data" {
  bucket                  = aws_s3_bucket.cur_data.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "cur_data" {
  bucket = aws_s3_bucket.cur_data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "cur_data" {
  bucket = aws_s3_bucket.cur_data.id

  rule {
    id     = "archive-and-expire"
    status = "Enabled"
    filter {}

    transition {
      days          = 90
      storage_class = "STANDARD_IA"
    }

    expiration {
      days = 365
    }
  }
}

resource "aws_s3_bucket_policy" "cur_data" {
  bucket = aws_s3_bucket.cur_data.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowDataExportsDelivery"
        Effect    = "Allow"
        Principal = { Service = "bcm-data-exports.amazonaws.com" }
        Action = [
          "s3:PutObject",
          "s3:GetBucketPolicy",
        ]
        Resource = [
          aws_s3_bucket.cur_data.arn,
          "${aws_s3_bucket.cur_data.arn}/*",
        ]
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = var.aws_account_id
          }
        }
      },
    ]
  })
}

################################################################################
# S3 — Athena query results bucket
################################################################################

resource "aws_s3_bucket" "athena_results" {
  bucket = local.athena_bucket_name
}

resource "aws_s3_bucket_public_access_block" "athena_results" {
  bucket                  = aws_s3_bucket.athena_results.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "athena_results" {
  bucket = aws_s3_bucket.athena_results.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "athena_results" {
  bucket = aws_s3_bucket.athena_results.id

  rule {
    id     = "expire-query-results"
    status = "Enabled"
    filter {}

    expiration {
      days = 7
    }
  }
}

################################################################################
# CUR 2.0 export via Data Exports (must be in us-east-1)
################################################################################

resource "aws_bcmdataexports_export" "cur" {
  provider = aws.us_east_1

  export {
    name = local.cur_report_name

    data_query {
      # Column names from: aws bcm-data-exports get-table --table-name COST_AND_USAGE_REPORT
      # with INCLUDE_RESOURCES=TRUE. resource_tags is a single map column in CUR 2.0.
      query_statement = "SELECT identity_line_item_id, identity_time_interval, bill_bill_type, bill_payer_account_id, bill_billing_period_start_date, bill_billing_period_end_date, line_item_usage_account_id, line_item_line_item_type, line_item_usage_start_date, line_item_usage_end_date, line_item_product_code, line_item_usage_type, line_item_operation, line_item_availability_zone, line_item_resource_id, line_item_usage_amount, line_item_currency_code, line_item_unblended_rate, line_item_unblended_cost, line_item_blended_rate, line_item_blended_cost, line_item_line_item_description, product_product_family, product_region_code, product_instance_type, product_servicecode, pricing_unit, pricing_public_on_demand_cost, pricing_public_on_demand_rate, pricing_term, resource_tags FROM COST_AND_USAGE_REPORT"

      table_configurations = {
        COST_AND_USAGE_REPORT = {
          TIME_GRANULARITY                      = "DAILY"
          INCLUDE_RESOURCES                     = "TRUE"
          INCLUDE_MANUAL_DISCOUNT_COMPATIBILITY = "FALSE"
          INCLUDE_SPLIT_COST_ALLOCATION_DATA    = "FALSE"
          # AWS returns this in response even when unset — must declare to
          # avoid "inconsistent result after apply" provider bug (issue #42761)
          BILLING_VIEW_ARN = "arn:aws:billing::${var.aws_account_id}:billingview/primary"
        }
      }
    }

    destination_configurations {
      s3_destination {
        s3_bucket = aws_s3_bucket.cur_data.id
        s3_prefix = local.cur_s3_prefix
        s3_region = var.region

        s3_output_configurations {
          overwrite   = "OVERWRITE_REPORT"
          format      = "PARQUET"
          compression = "PARQUET"
          output_type = "CUSTOM"
        }
      }
    }

    refresh_cadence {
      frequency = "SYNCHRONOUS"
    }
  }
}

################################################################################
# Glue — catalog database + crawler
################################################################################

resource "aws_glue_catalog_database" "cur" {
  name = local.glue_database_name
}

resource "aws_iam_role" "cur_crawler" {
  name                 = local.crawler_role_name
  permissions_boundary = var.org_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "glue.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "cur_crawler_glue" {
  role       = aws_iam_role.cur_crawler.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSGlueServiceRole"
}

resource "aws_iam_role_policy" "cur_crawler_s3" {
  name = "${local.crawler_role_name}-s3"
  role = aws_iam_role.cur_crawler.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid    = "ReadCURData"
      Effect = "Allow"
      Action = [
        "s3:GetObject",
        "s3:ListBucket",
      ]
      Resource = [
        aws_s3_bucket.cur_data.arn,
        "${aws_s3_bucket.cur_data.arn}/*",
      ]
    }]
  })
}

resource "aws_glue_crawler" "cur" {
  name          = "${var.project}-cur-crawler"
  database_name = aws_glue_catalog_database.cur.name
  role          = aws_iam_role.cur_crawler.arn
  schedule      = "cron(0 6 * * ? *)" # Daily at 06:00 UTC, before 08:00 reports

  s3_target {
    path = "s3://${aws_s3_bucket.cur_data.id}/${local.cur_s3_prefix}/"
  }

  schema_change_policy {
    update_behavior = "UPDATE_IN_DATABASE"
    delete_behavior = "DELETE_FROM_DATABASE"
  }

  configuration = jsonencode({
    Version = 1.0
    Grouping = {
      TableGroupingPolicy = "CombineCompatibleSchemas"
    }
  })
}

################################################################################
# Athena workgroup
################################################################################

resource "aws_athena_workgroup" "cur" {
  name = local.athena_workgroup

  configuration {
    enforce_workgroup_configuration = true
    bytes_scanned_cutoff_per_query  = 104857600 # 100 MB safety limit

    result_configuration {
      output_location = "s3://${aws_s3_bucket.athena_results.id}/"

      encryption_configuration {
        encryption_option = "SSE_S3"
      }
    }
  }
}

################################################################################
# Billing protection — account-level AWS Budget (notifications only)
################################################################################

resource "aws_budgets_budget" "account" {
  name         = "${var.project}-account-monthly"
  budget_type  = "COST"
  limit_amount = tostring(var.account_budget_usd)
  limit_unit   = "USD"
  time_unit    = "MONTHLY"

  # Early warning: AWS forecasts the month will end above the budget.
  notification {
    comparison_operator       = "GREATER_THAN"
    notification_type         = "FORECASTED"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    subscriber_sns_topic_arns = [var.alerts_topic_arn]
  }

  # Budget exceeded.
  notification {
    comparison_operator       = "GREATER_THAN"
    notification_type         = "ACTUAL"
    threshold                 = 100
    threshold_type            = "PERCENTAGE"
    subscriber_sns_topic_arns = [var.alerts_topic_arn]
  }

  # Critical: spend is 50% over budget. Something is running away.
  notification {
    comparison_operator       = "GREATER_THAN"
    notification_type         = "ACTUAL"
    threshold                 = 150
    threshold_type            = "PERCENTAGE"
    subscriber_sns_topic_arns = [var.alerts_topic_arn]
  }
}
