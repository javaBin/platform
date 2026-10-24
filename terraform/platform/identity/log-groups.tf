# Bounded retention for the auth-claims Lambda's log group (auto-created by
# Lambda with "Never expire"; adopted by terraform/platform/imports.tf).
resource "aws_cloudwatch_log_group" "auth_claims" {
  name              = "/aws/lambda/${var.project}-auth-claims"
  retention_in_days = 365
}
