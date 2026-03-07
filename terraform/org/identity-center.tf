################################################################################
# IAM Identity Center — Permission Sets + Group Assignments
#
# Security-critical: applied manually by admins, never by CI.
# The javabin-ci-infra role has explicit deny on sso:*.
#
# Prerequisite: Google Workspace SAML sync must be configured first so that
# Identity Center groups (infra@, heroes@, board@, pkom@) exist.
#
# ABAC: The "team" attribute flows from Google group membership through
# SAML assertion → Identity Center → STS session tag. This lets a single
# JavabinDeveloper permission set work for all teams.
################################################################################

data "aws_ssoadmin_instances" "this" {}

locals {
  instance_arn = tolist(data.aws_ssoadmin_instances.this.arns)[0]
}

################################################################################
# Access Control Attributes (ABAC) — map "team" from SAML assertion
################################################################################

resource "aws_ssoadmin_instance_access_control_attributes" "this" {
  instance_arn = local.instance_arn

  attribute {
    key = "team"
    value {
      source = ["$${path:enterprise.team}"]
    }
  }
}

################################################################################
# Permission Set: JavabinAdmin
# Full admin for infra@java.no. No permission boundary (trusted).
################################################################################

resource "aws_ssoadmin_permission_set" "admin" {
  name             = "${var.project}-admin"
  description      = "Full administrator access for platform infrastructure team"
  instance_arn     = local.instance_arn
  session_duration = var.session_duration
}

resource "aws_ssoadmin_managed_policy_attachment" "admin" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.admin.arn
  managed_policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

################################################################################
# Permission Set: JavabinDeveloper
# ABAC-scoped access for heroes. Uses permission boundary.
# Team tag from SAML assertion enables access to team-tagged resources.
################################################################################

resource "aws_ssoadmin_permission_set" "developer" {
  name             = "${var.project}-developer"
  description      = "Developer access scoped to team resources via ABAC"
  instance_arn     = local.instance_arn
  session_duration = var.session_duration
}

resource "aws_ssoadmin_permissions_boundary_attachment" "developer" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.developer.arn

  permissions_boundary {
    customer_managed_policy_reference {
      name = "${var.project}-developer-boundary"
    }
  }
}

resource "aws_ssoadmin_permission_set_inline_policy" "developer" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.developer.arn

  inline_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadOwnTeamResources"
        Effect = "Allow"
        Action = [
          "ecs:Describe*",
          "ecs:List*",
          "ecr:Describe*",
          "ecr:List*",
          "ecr:GetRepositoryPolicy",
          "ecr:BatchGetImage",
          "s3:GetObject",
          "s3:ListBucket",
          "s3:GetBucketLocation",
          "dynamodb:GetItem",
          "dynamodb:Query",
          "dynamodb:Scan",
          "dynamodb:DescribeTable",
          "sqs:GetQueueAttributes",
          "sqs:ListQueues",
          "secretsmanager:DescribeSecret",
          "secretsmanager:ListSecrets",
          "logs:GetLogEvents",
          "logs:FilterLogEvents",
          "logs:DescribeLogGroups",
          "logs:DescribeLogStreams",
          "cloudwatch:GetMetricData",
          "cloudwatch:DescribeAlarms",
          "cloudwatch:ListMetrics",
        ]
        Resource = "*"
        Condition = {
          StringEqualsIfExists = {
            "aws:ResourceTag/team" = "$${aws:PrincipalTag/team}"
          }
        }
      },
      {
        Sid    = "ReadPlatformSharedResources"
        Effect = "Allow"
        Action = [
          "ecs:ListClusters",
          "ecs:ListServices",
          "ecr:GetAuthorizationToken",
          "elasticloadbalancing:Describe*",
          "route53:ListHostedZones",
          "route53:GetHostedZone",
        ]
        Resource = "*"
      },
      {
        Sid    = "CostExplorer"
        Effect = "Allow"
        Action = [
          "ce:GetCostAndUsage",
          "ce:GetCostForecast",
        ]
        # ce:* requires Resource = "*" per AWS docs
        Resource = "*"
      },
      {
        Sid    = "SSMAppParameters"
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters",
          "ssm:GetParametersByPath",
        ]
        Resource = "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/platform-apps/*"
      },
    ]
  })
}

################################################################################
# Permission Set: JavabinReadOnly
# Governance visibility for board and program committee.
################################################################################

resource "aws_ssoadmin_permission_set" "readonly" {
  name             = "${var.project}-readonly"
  description      = "Read-only governance access for board and program committee"
  instance_arn     = local.instance_arn
  session_duration = var.session_duration
}

resource "aws_ssoadmin_managed_policy_attachment" "readonly" {
  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.readonly.arn
  managed_policy_arn = "arn:aws:iam::aws:policy/job-function/ViewOnlyAccess"
}

################################################################################
# Group → Permission Set Assignments
#
# TODO: Add group lookups and assignments after Google SAML sync is configured
# and Identity Center groups (infra@, heroes@, board@, pkom@) exist.
################################################################################
