################################################################################
# Platform secrets — SSM SecureString parameters
#
# Terraform owns the parameter (name, type, tags); the value is set out-of-band:
#   aws ssm put-parameter --name <name> --type SecureString --value <v> --overwrite
################################################################################

resource "aws_ssm_parameter" "atlassian_org_id" {
  name        = "/${var.project}/platform/atlassian-org-id"
  description = "Atlassian organisation ID"
  type        = "SecureString"
  value       = "PLACEHOLDER"

  lifecycle {
    ignore_changes = [value]
  }
}

resource "aws_ssm_parameter" "atlassian_api_key" {
  name        = "/${var.project}/platform/atlassian-api-key"
  description = "Atlassian organisation admin API key"
  type        = "SecureString"
  value       = "PLACEHOLDER"

  lifecycle {
    ignore_changes = [value]
  }
}
