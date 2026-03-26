################################################################################
# Permission Boundary: javabin-org-boundary
#
# Structural safety net for ALL roles in the account. Carried by platform
# roles (infra, plan, override-approver, registry, apply-gate) and cross-team
# Lambda roles (resource-tagger, budget-enforcer, apply-gate, ci-broker).
#
# Contains ONLY account-level structural denies — things that are dangerous
# regardless of team scope. Team ABAC isolation lives in the developer
# boundary (terraform/platform/iam/boundary.tf), which is a strict superset.
#
# IMPORTANT: Human-applied. The self-protection deny prevents any role
# carrying this boundary from modifying it.
################################################################################

resource "aws_iam_policy" "org_boundary" {
  name        = "${var.project}-org-boundary"
  description = "Structural permission boundary for platform roles. Denies account-level dangerous operations."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      ########################################################################
      # Allow everything not explicitly denied below
      ########################################################################
      {
        Sid      = "AllowAll"
        Effect   = "Allow"
        Action   = "*"
        Resource = "*"
      },

      ########################################################################
      # Self-protection: deny modifying this boundary policy
      ########################################################################
      {
        Sid    = "DenyOrgBoundaryTamper"
        Effect = "Deny"
        Action = [
          "iam:DeletePolicy",
          "iam:DeletePolicyVersion",
          "iam:CreatePolicyVersion",
          "iam:SetDefaultPolicyVersion"
        ]
        Resource = "arn:aws:iam::${var.aws_account_id}:policy/${var.project}-org-boundary"
      },

      ########################################################################
      # Self-replicating: roles can only create roles with one of the two
      # boundaries. Infra role needs to create team roles (developer boundary)
      # and platform roles (org boundary).
      ########################################################################
      {
        Sid    = "DenyNoBoundary"
        Effect = "Deny"
        Action = [
          "iam:CreateRole",
          "iam:PutRolePermissionsBoundary"
        ]
        Resource = "*"
        Condition = {
          StringNotEquals = {
            "iam:PermissionsBoundary" = [
              "arn:aws:iam::${var.aws_account_id}:policy/${var.project}-org-boundary",
              "arn:aws:iam::${var.aws_account_id}:policy/${var.project}-developer-boundary",
            ]
          }
        }
      },

      ########################################################################
      # Deny removing the boundary from any role
      ########################################################################
      {
        Sid      = "DenyDeleteBoundary"
        Effect   = "Deny"
        Action   = "iam:DeleteRolePermissionsBoundary"
        Resource = "*"
      },

      ########################################################################
      # Deny creating IAM users and access keys
      ########################################################################
      {
        Sid    = "DenyIAMUsers"
        Effect = "Deny"
        Action = [
          "iam:CreateUser",
          "iam:CreateLoginProfile",
          "iam:UpdateLoginProfile",
          "iam:CreateAccessKey",
          "iam:DeleteAccountPasswordPolicy",
          "iam:CreateVirtualMFADevice",
          "iam:DeactivateMFADevice"
        ]
        Resource = "*"
      },

      ########################################################################
      # Deny IAM Identity Center, Organizations, SCPs
      ########################################################################
      {
        Sid    = "DenyOrgAndSSO"
        Effect = "Deny"
        Action = [
          "organizations:*",
          "sso:*",
          "sso-directory:*",
          "identitystore:*",
          "account:*"
        ]
        Resource = "*"
      },

      ########################################################################
      # Deny disabling/deleting protective services
      #
      # Specific destructive actions only — NOT wildcards. The infra role
      # carries this boundary and needs to manage these services (create
      # detectors, recorders, etc.).
      ########################################################################
      {
        Sid    = "DenyProtectiveSvcs"
        Effect = "Deny"
        Action = [
          "guardduty:DeleteDetector",
          "guardduty:DeleteMembers",
          "guardduty:DisassociateFromMasterAccount",
          "guardduty:UpdateDetector",
          "config:DeleteConfigurationRecorder",
          "config:StopConfigurationRecorder",
          "config:DeleteDeliveryChannel",
          "config:DeleteRetentionConfiguration",
          "securityhub:DisableSecurityHub",
          "securityhub:DeleteMembers",
          "securityhub:DisassociateFromMasterAccount",
          "cloudtrail:DeleteTrail",
          "cloudtrail:StopLogging",
          "cloudtrail:UpdateTrail",
          "cloudtrail:PutEventSelectors"
        ]
        Resource = "*"
      },

      ########################################################################
      # Deny platform networking: VPC, subnets, IGW, NAT gateway
      ########################################################################
      {
        Sid    = "DenyNetworking"
        Effect = "Deny"
        Action = [
          "ec2:CreateVpc",
          "ec2:DeleteVpc",
          "ec2:ModifyVpcAttribute",
          "ec2:CreateSubnet",
          "ec2:DeleteSubnet",
          "ec2:CreateInternetGateway",
          "ec2:DeleteInternetGateway",
          "ec2:AttachInternetGateway",
          "ec2:DetachInternetGateway",
          "ec2:CreateNatGateway",
          "ec2:DeleteNatGateway",
          "ec2:CreateRouteTable",
          "ec2:DeleteRouteTable"
        ]
        Resource = "*"
      },
    ]
  })

  tags = {
    Name = "${var.project}-org-boundary"
  }
}
