################################################################################
# DynamoDB Table — standard config for service data
################################################################################

# {team}-{service}-{name}: two apps in one team can both have a "sessions" table.
resource "aws_dynamodb_table" "this" {
  name         = "${var.team}-${var.service}-${var.name}"
  billing_mode = var.billing_mode
  hash_key     = var.hash_key

  attribute {
    name = var.hash_key
    type = var.hash_key_type
  }

  dynamic "attribute" {
    for_each = var.range_key != null ? [var.range_key] : []
    content {
      name = attribute.value
      type = var.range_key_type
    }
  }

  range_key = var.range_key

  point_in_time_recovery {
    enabled = var.point_in_time_recovery
  }

  server_side_encryption {
    enabled = true
  }

  ttl {
    attribute_name = var.ttl_attribute
    enabled        = var.ttl_attribute != ""
  }

  tags = {
    Name    = "${var.team}-${var.service}-${var.name}"
    service = var.service
  }
}

################################################################################
# IAM Policy Document — for attaching to task roles
################################################################################

data "aws_iam_policy_document" "access" {
  statement {
    sid    = "TableAccess"
    effect = "Allow"
    actions = [
      "dynamodb:GetItem",
      "dynamodb:PutItem",
      "dynamodb:UpdateItem",
      "dynamodb:DeleteItem",
      "dynamodb:Query",
      "dynamodb:Scan",
      "dynamodb:BatchGetItem",
      "dynamodb:BatchWriteItem",
    ]
    resources = [
      aws_dynamodb_table.this.arn,
      "${aws_dynamodb_table.this.arn}/index/*",
    ]
  }
}
