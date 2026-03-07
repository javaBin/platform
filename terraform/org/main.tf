################################################################################
# Org module — AWS Organizations + SCP
#
# This module is NEVER applied by CI. It requires a human with admin
# credentials (Identity Center or IAM) to run terraform apply.
#
# See docs/org-runbook.md for instructions.
#
# IMPORTANT: Do NOT apply the SCP until:
#   1. The javabin-developer-boundary IAM policy exists
#   2. The javabin-ci-infra role exists and references the boundary
#   3. You have verified the exempt_roles variable is correct
################################################################################

################################################################################
# AWS Organization
################################################################################

resource "aws_organizations_organization" "main" {
  feature_set = "ALL"

  # Enable SCPs
  enabled_policy_types = ["SERVICE_CONTROL_POLICY"]

  # sso.amazonaws.com is required for Identity Center
  aws_service_access_principals = ["sso.amazonaws.com"]
}

################################################################################
# SCP: Require permission boundary on role creation
#
# Any iam:CreateRole or iam:PutRolePermissionsBoundary call must attach the
# javabin-developer-boundary policy. Exempt roles (platform CI, root) are
# excluded via condition on the caller's ARN.
################################################################################

resource "aws_organizations_policy" "require_boundary" {
  name        = "${var.project}-require-permission-boundary"
  description = "Deny role creation unless javabin-developer-boundary is attached. Exempts platform CI and root."
  type        = "SERVICE_CONTROL_POLICY"

  content = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "RequirePermissionBoundary"
        Effect = "Deny"
        Action = [
          "iam:CreateRole",
          "iam:PutRolePermissionsBoundary"
        ]
        Resource = "*"
        Condition = {
          StringNotEquals = {
            "iam:PermissionsBoundary" = var.boundary_policy_arn
          }
          # Exempt platform CI role and root from this requirement
          ArnNotLike = {
            "aws:PrincipalARN" = var.exempt_roles
          }
        }
      }
    ]
  })
}

################################################################################
# Attach SCP to the root of the organization
################################################################################

resource "aws_organizations_policy_attachment" "require_boundary" {
  policy_id = aws_organizations_policy.require_boundary.id
  target_id = aws_organizations_organization.main.roots[0].id
}
