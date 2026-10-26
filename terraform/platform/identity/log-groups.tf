# Bounded retention for the auth-claims Lambda's log group (auto-created by
# Lambda with "Never expire"; adopted by terraform/platform/imports.tf).
resource "aws_cloudwatch_log_group" "auth_claims" {
  name              = "/aws/lambda/${var.project}-auth-claims"
  retention_in_days = 365
}

# The external pool's trigger (javabin-auth-claims-external) had not logged
# yet, so its group is created fresh rather than imported.
resource "aws_cloudwatch_log_group" "auth_claims_external" {
  name              = "/aws/lambda/${var.project}-auth-claims-external"
  retention_in_days = 365
}
