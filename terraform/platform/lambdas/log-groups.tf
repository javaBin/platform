################################################################################
# CloudWatch log groups for the platform Lambdas
#
# Lambda auto-creates /aws/lambda/<name> with "Never expire" when a function
# first logs. Declaring the groups here bounds retention and puts them under
# Terraform. Existing groups are adopted by the import blocks in
# terraform/platform/imports.tf.
#
# Every aws_lambda_function in this module must have its short name listed in
# local.platform_lambda_names (scripts/tests/test_lambda_log_groups.py checks).
################################################################################

locals {
  platform_lambda_names = toset([
    "app-auth-sync",
    "app-secret-rollout",
    "apply-gate",
    "budget-enforcer",
    "ci-broker",
    "compliance-reporter",
    "cost-report",
    "daily-cost-check",
    "override-cleanup",
    "password-set",
    "plan-review",
    "resource-tagger",
    "securityhub-summary",
    "slack-alert",
    "team-provisioner",
  ])

  # 365 days: keeps a year of audit trail for apply-gate, ci-broker and
  # password-set. Platform Lambda logs are ~80 MB in total, so cost is negligible.
  platform_lambda_log_retention_days = 365
}

resource "aws_cloudwatch_log_group" "lambda" {
  for_each = local.platform_lambda_names

  name              = "/aws/lambda/${var.project}-${each.key}"
  retention_in_days = local.platform_lambda_log_retention_days
}
