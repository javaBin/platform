################################################################################
# Permission Boundary: javabin-developer-boundary
#
# MIGRATION NOTE: This resource is being moved to terraform/org/boundary.tf
# because the boundary's self-protection (DenyBoundaryTampering) prevents
# the CI pipeline from modifying it. See docs/org-runbook.md for migration steps.
#
# Once the state migration is complete, this file will be replaced with a
# data source reference. Until then, keep this resource to avoid CI destroying it.
################################################################################

resource "aws_iam_policy" "developer_boundary" {
  name        = "${var.project}-developer-boundary"
  description = "Permission boundary for all non-platform roles. Self-replicating: roles with this boundary can only create roles that also carry it."

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
      # Self-replicating: deny creating/modifying roles without this boundary
      ########################################################################
      {
        Sid    = "DenyRolesWithoutBoundary"
        Effect = "Deny"
        Action = [
          "iam:CreateRole",
          "iam:PutRolePermissionsBoundary"
        ]
        Resource = "*"
        Condition = {
          StringNotEquals = {
            "iam:PermissionsBoundary" = "arn:aws:iam::${var.aws_account_id}:policy/${var.project}-developer-boundary"
          }
        }
      },

      ########################################################################
      # Deny creating IAM users and access keys (console/programmatic)
      ########################################################################
      {
        Sid    = "DenyIAMUserCreation"
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
      # Deny modifying or deleting this boundary policy itself
      ########################################################################
      {
        Sid    = "DenyBoundaryTampering"
        Effect = "Deny"
        Action = [
          "iam:DeletePolicy",
          "iam:DeletePolicyVersion",
          "iam:CreatePolicyVersion",
          "iam:SetDefaultPolicyVersion"
        ]
        Resource = "arn:aws:iam::${var.aws_account_id}:policy/${var.project}-developer-boundary"
      },
      {
        Sid    = "DenyDeleteRoleBoundary"
        Effect = "Deny"
        Action = [
          "iam:DeleteRolePermissionsBoundary"
        ]
        Resource = "*"
      },

      ########################################################################
      # Deny IAM Identity Center, Organizations, SCPs
      ########################################################################
      {
        Sid    = "DenyIdentityCenterAndOrgs"
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
      ########################################################################
      {
        Sid    = "DenyProtectiveServicesTampering"
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
        Sid    = "DenyPlatformNetworking"
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

      ########################################################################
      # Protect platform security groups
      ########################################################################
      {
        Sid    = "DenyPlatformSecurityGroups"
        Effect = "Deny"
        Action = [
          "ec2:DeleteSecurityGroup",
          "ec2:AuthorizeSecurityGroupIngress",
          "ec2:RevokeSecurityGroupIngress",
          "ec2:AuthorizeSecurityGroupEgress",
          "ec2:RevokeSecurityGroupEgress",
          "ec2:ModifySecurityGroupRules",
        ]
        Resource = "arn:aws:ec2:${var.region}:${var.aws_account_id}:security-group/*"
        Condition = {
          StringLike = {
            "ec2:ResourceTag/Name" = "${var.project}-*"
          }
        }
      },

      ########################################################################
      # Deny platform ECS cluster, ALB, ACM certs
      ########################################################################
      {
        Sid    = "DenyPlatformECSCluster"
        Effect = "Deny"
        Action = [
          "ecs:DeleteCluster",
          "ecs:UpdateCluster"
        ]
        Resource = "arn:aws:ecs:${var.region}:${var.aws_account_id}:cluster/${var.project}-platform"
      },
      {
        Sid    = "DenyPlatformALB"
        Effect = "Deny"
        Action = [
          "elasticloadbalancing:DeleteLoadBalancer",
          "elasticloadbalancing:ModifyLoadBalancerAttributes"
        ]
        Resource = "arn:aws:elasticloadbalancing:${var.region}:${var.aws_account_id}:loadbalancer/app/${var.project}-*"
      },
      {
        Sid    = "DenyPlatformACM"
        Effect = "Deny"
        Action = [
          "acm:DeleteCertificate"
        ]
        Resource = "arn:aws:acm:${var.region}:${var.aws_account_id}:certificate/*"
      },

      ########################################################################
      # Deny access to state and CI artifact buckets/tables
      ########################################################################
      {
        Sid    = "DenyStateBuckets"
        Effect = "Deny"
        Action = [
          "s3:DeleteBucket",
          "s3:PutBucketPolicy",
          "s3:DeleteBucketPolicy",
          "s3:PutBucketVersioning",
          "s3:PutEncryptionConfiguration"
        ]
        Resource = [
          "arn:aws:s3:::${var.project}-terraform-*",
          "arn:aws:s3:::${var.project}-ci-*"
        ]
      },
      {
        Sid    = "DenyStateTables"
        Effect = "Deny"
        Action = [
          "dynamodb:DeleteTable",
          "dynamodb:UpdateTable"
        ]
        Resource = "arn:aws:dynamodb:${var.region}:${var.aws_account_id}:table/${var.project}-terraform-*"
      }
    ]
  })

  tags = {
    Name = "${var.project}-developer-boundary"
  }
}
