################################################################################
# Permission Boundary: javabin-developer-boundary
#
# Superset of the org boundary. Carried by team CI roles, deploy roles,
# app-broker, and app-created roles.
#
# Contains ALL org structural denies PLUS team ABAC isolation, naming
# enforcement, and platform resource protection.
#
# Self-protecting: roles carrying this boundary cannot modify it.
# The infra role (which carries the org boundary, NOT this boundary)
# CAN modify it — enabling CI to update ABAC rules without human apply.
#
# Uses ${aws:PrincipalTag/team} ABAC variables throughout, so a single
# policy works for all teams.
#
# AWS managed policy limit: 6,144 bytes. Keep statements compact.
################################################################################

data "aws_iam_policy" "org_boundary" {
  name = "${var.project}-org-boundary"
}

resource "aws_iam_policy" "developer_boundary" {
  name        = "${var.project}-developer-boundary"
  description = "Team permission boundary: org denies + ABAC isolation + naming enforcement."

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # -- BASELINE --
      {
        Sid      = "Allow"
        Effect   = "Allow"
        Action   = "*"
        Resource = "*"
      },
      # -- STRUCTURAL (superset of org boundary) --
      {
        Sid    = "DenyBndMod"
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
        Sid      = "DenyNoBnd"
        Effect   = "Deny"
        Action   = ["iam:CreateRole", "iam:PutRolePermissionsBoundary"]
        Resource = "*"
        Condition = {
          StringNotEquals = {
            "iam:PermissionsBoundary" = "arn:aws:iam::${var.aws_account_id}:policy/${var.project}-developer-boundary"
          }
        }
      },
      {
        Sid      = "DenyStripBnd"
        Effect   = "Deny"
        Action   = "iam:DeleteRolePermissionsBoundary"
        Resource = "*"
      },
      {
        Sid    = "DenyIAMUsers"
        Effect = "Deny"
        Action = [
          "iam:CreateUser", "iam:CreateLoginProfile", "iam:UpdateLoginProfile",
          "iam:CreateAccessKey", "iam:DeactivateMFADevice"
        ]
        Resource = "*"
      },
      {
        Sid      = "DenyOrgSSO"
        Effect   = "Deny"
        Action   = ["organizations:*", "sso:*", "sso-directory:*", "identitystore:*", "account:*"]
        Resource = "*"
      },
      {
        Sid    = "DenyNetwork"
        Effect = "Deny"
        Action = [
          "ec2:CreateVpc", "ec2:DeleteVpc", "ec2:ModifyVpcAttribute",
          "ec2:CreateSubnet", "ec2:DeleteSubnet",
          "ec2:CreateInternetGateway", "ec2:DeleteInternetGateway",
          "ec2:AttachInternetGateway", "ec2:DetachInternetGateway",
          "ec2:CreateNatGateway", "ec2:DeleteNatGateway",
          "ec2:CreateRouteTable", "ec2:DeleteRouteTable"
        ]
        Resource = "*"
      },
      # -- PLATFORM PROTECTION --
      # Platform SGs: covered by DenyMutateShared (team=shared) + DenyTagStrip
      # Platform ACM: covered by DenyCrossTeam (team=platform)
      {
        Sid    = "DenyPlatInfra"
        Effect = "Deny"
        Action = [
          "ecs:DeleteCluster", "ecs:UpdateCluster",
          "elasticloadbalancing:DeleteLoadBalancer", "elasticloadbalancing:ModifyLoadBalancerAttributes",
          "s3:DeleteBucket", "s3:PutBucketPolicy", "s3:DeleteBucketPolicy",
          "s3:PutBucketVersioning", "s3:PutEncryptionConfiguration",
          "dynamodb:DeleteTable", "dynamodb:UpdateTable"
        ]
        Resource = [
          "arn:aws:ecs:${var.region}:${var.aws_account_id}:cluster/${var.project}-platform",
          "arn:aws:elasticloadbalancing:${var.region}:${var.aws_account_id}:loadbalancer/app/${var.project}-*",
          "arn:aws:s3:::${var.project}-terraform-*",
          "arn:aws:s3:::${var.project}-ci-*",
          "arn:aws:dynamodb:${var.region}:${var.aws_account_id}:table/${var.project}-terraform-*",
        ]
      },
      {
        # Security services + Bedrock (platform-only for plan review/cost narratives)
        Sid      = "DenySecSvcs"
        Effect   = "Deny"
        Action   = ["guardduty:*", "securityhub:*", "config:*", "cloudtrail:*", "bedrock:Invoke*", "bedrock:Converse*"]
        Resource = "*"
      },
      # -- TEAM ABAC --
      {
        Sid    = "DenyXTeam"
        Effect = "Deny"
        Action = "*"
        NotResource = [
          "arn:aws:s3:::${var.project}-terraform-state-${var.aws_account_id}",
          "arn:aws:s3:::${var.project}-terraform-state-${var.aws_account_id}/*",
          "arn:aws:dynamodb:${var.region}:${var.aws_account_id}:table/${var.project}-terraform-app-locks",
          "arn:aws:s3:::${var.project}-ci-plan-artifacts-${var.aws_account_id}",
          "arn:aws:s3:::${var.project}-ci-plan-artifacts-${var.aws_account_id}/*",
        ]
        Condition = {
          StringNotEquals = {
            "aws:ResourceTag/team" = ["$${aws:PrincipalTag/team}", "shared"]
          }
          "Null" = { "aws:ResourceTag/team" = "false" }
        }
      },
      {
        Sid    = "DenyShared"
        Effect = "Deny"
        Action = [
          "ec2:Delete*", "ec2:Modify*",
          "elasticloadbalancing:Delete*", "elasticloadbalancing:Modify*",
          "ecs:DeleteCluster", "ecs:UpdateCluster", "ecs:UpdateClusterSettings",
          "iam:DeleteRole", "iam:UpdateRole", "iam:DeleteRolePolicy",
          "iam:PutRolePolicy", "iam:AttachRolePolicy", "iam:DetachRolePolicy",
          "route53:DeleteHostedZone", "sns:DeleteTopic", "sns:SetTopicAttributes",
        ]
        Resource  = "*"
        Condition = { StringEquals = { "aws:ResourceTag/team" = "shared" } }
      },
      {
        # Only services with shared/platform-tagged resources teams can access.
        # Other services (lambda, sqs, dynamodb, etc.) already blocked by DenyCrossTeam.
        Sid    = "DenyTagStrip"
        Effect = "Deny"
        Action = [
          "ec2:DeleteTags", "ec2:CreateTags",
          "ecs:TagResource", "ecs:UntagResource",
          "elasticloadbalancing:AddTags", "elasticloadbalancing:RemoveTags",
          "iam:TagRole", "iam:UntagRole",
          "sns:TagResource", "sns:UntagResource",
        ]
        Resource  = "*"
        Condition = { StringEquals = { "aws:ResourceTag/team" = ["shared", "platform"] } }
      },
      {
        Sid    = "DenyPlatSSM"
        Effect = "Deny"
        Action = ["ssm:GetParameter*", "ssm:GetParametersByPath"]
        Resource = [
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/platform/*",
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/slack/*",
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/platform-overrides/*",
        ]
      },
      {
        Sid    = "DenyPassRole"
        Effect = "Deny"
        Action = "iam:PassRole"
        NotResource = [
          "arn:aws:iam::${var.aws_account_id}:role/$${aws:PrincipalTag/team}-*",
          "arn:aws:iam::${var.aws_account_id}:role/${var.project}-ecs-execution",
        ]
      },
      {
        Sid    = "DenyXLogs"
        Effect = "Deny"
        Action = ["logs:GetLogEvents", "logs:FilterLogEvents", "logs:StartQuery", "logs:GetQueryResults"]
        NotResource = [
          "arn:aws:logs:${var.region}:${var.aws_account_id}:log-group:/ecs/$${aws:PrincipalTag/team}/*",
          "arn:aws:logs:${var.region}:${var.aws_account_id}:log-group:/ecs/$${aws:PrincipalTag/team}/*:*",
        ]
      },
      {
        Sid    = "DenyModCI"
        Effect = "Deny"
        Action = [
          "iam:PutRolePolicy", "iam:DeleteRolePolicy",
          "iam:AttachRolePolicy", "iam:DetachRolePolicy",
          "iam:DeleteRole", "iam:UpdateRole", "iam:UpdateAssumeRolePolicy"
        ]
        Resource = "arn:aws:iam::${var.aws_account_id}:role/${var.project}-ci-*"
      },
      # -- REQUEST TAG ENFORCEMENT --
      # SGs use IDs in ARNs so DenyBadName can't enforce naming.
      # Require team tag on create — Terraform default_tags handles this.
      {
        Sid      = "DenyNoTag"
        Effect   = "Deny"
        Action   = "ec2:CreateSecurityGroup"
        Resource = "*"
        Condition = {
          StringNotEqualsIfExists = {
            "aws:RequestTag/team" = "$${aws:PrincipalTag/team}"
          }
        }
      },
      # -- NAMING ENFORCEMENT --
      {
        Sid    = "DenyBadName"
        Effect = "Deny"
        Action = [
          "s3:CreateBucket", "dynamodb:CreateTable", "sqs:CreateQueue", "sns:CreateTopic",
          "rds:CreateDBInstance", "rds:CreateDBSubnetGroup",
          "ecs:CreateService", "ecs:RegisterTaskDefinition",
          "ecr:CreateRepository", "logs:CreateLogGroup", "ssm:PutParameter",
          "lambda:CreateFunction", "events:PutRule", "states:CreateStateMachine",
        ]
        NotResource = [
          "arn:aws:s3:::$${aws:PrincipalTag/team}-*",
          "arn:aws:dynamodb:${var.region}:${var.aws_account_id}:table/$${aws:PrincipalTag/team}-*",
          "arn:aws:sqs:${var.region}:${var.aws_account_id}:$${aws:PrincipalTag/team}-*",
          "arn:aws:sns:${var.region}:${var.aws_account_id}:$${aws:PrincipalTag/team}-*",
          "arn:aws:rds:${var.region}:${var.aws_account_id}:db:$${aws:PrincipalTag/team}-*",
          "arn:aws:rds:${var.region}:${var.aws_account_id}:subgrp:$${aws:PrincipalTag/team}-*",
          "arn:aws:ecs:${var.region}:${var.aws_account_id}:service/*/$${aws:PrincipalTag/team}-*",
          "arn:aws:ecs:${var.region}:${var.aws_account_id}:task-definition/$${aws:PrincipalTag/team}-*:*",
          "arn:aws:ecr:${var.region}:${var.aws_account_id}:repository/$${aws:PrincipalTag/team}-*",
          "arn:aws:logs:${var.region}:${var.aws_account_id}:log-group:/ecs/$${aws:PrincipalTag/team}/*",
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/apps/$${aws:PrincipalTag/team}/*",
          "arn:aws:lambda:${var.region}:${var.aws_account_id}:function:$${aws:PrincipalTag/team}-*",
          "arn:aws:events:${var.region}:${var.aws_account_id}:rule/$${aws:PrincipalTag/team}-*",
          "arn:aws:states:${var.region}:${var.aws_account_id}:stateMachine:$${aws:PrincipalTag/team}-*",
        ]
      },
    ]
  })

  tags = {
    Name = "${var.project}-developer-boundary"
  }
}
