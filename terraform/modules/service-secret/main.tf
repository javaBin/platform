################################################################################
# SSM Parameter (SecureString) — app secret
################################################################################

resource "aws_ssm_parameter" "this" {
  name        = "/${var.project}/apps/${var.service}/${var.name}"
  description = var.description
  type        = "SecureString"
  value       = "PLACEHOLDER"

  tags = {
    Name    = "${var.project}-${var.name}"
    service = var.service
  }

  lifecycle {
    ignore_changes = [value]
  }
}

################################################################################
# IAM Policy Document — for attaching to task roles
################################################################################

data "aws_iam_policy_document" "access" {
  statement {
    sid    = "SSMSecretRead"
    effect = "Allow"
    actions = [
      "ssm:GetParameter",
      "ssm:GetParameters",
    ]
    resources = [
      aws_ssm_parameter.this.arn,
    ]
  }
}
