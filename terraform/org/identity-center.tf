################################################################################
# IAM Identity Center — Permission Sets + Group Assignments
#
# Security-critical: applied manually by admins, never by CI.
# The javabin-ci-infra role has explicit deny on sso:*.
#
# Prerequisite: Google Workspace SAML sync must be configured first so that
# Identity Center groups (infra@, heroes@, board@, pkom@) exist.
#
# ABAC: The "team" attribute was meant to flow from Google group membership
# through SAML assertion → Identity Center → STS session tag, so a single
# JavabinDeveloper permission set would work for all teams.
#
# NOTE: the "team" attribute is never populated on Identity Center users, so
# SSO sessions carry no team tag and javabin-developer gets no access to team
# resources. Developers use the per-team permission sets (javabin-team-{team})
# created by the team-provisioner Lambda instead (see CLAUDE.md, Known Issues).
################################################################################

data "aws_ssoadmin_instances" "this" {}

locals {
  instance_arn = tolist(data.aws_ssoadmin_instances.this.arns)[0]
}

################################################################################
# Access Control Attributes (ABAC) — map "team" from SAML assertion
# (the source attribute is never set today; see the ABAC note above)
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
# Baseline access for every developer (javabin-developer@ / kodesmia).
# The ABAC statements below never match today: SSO sessions carry no team tag
# (see CLAUDE.md "Known Issues"). Team resources come from the per-team
# javabin-team-{team} permission sets the team-provisioner creates.
################################################################################

resource "aws_ssoadmin_permission_set" "developer" {
  name             = "${var.project}-developer"
  description      = "Baseline developer access (metrics, costs, shared infra reads). Team resources: use javabin-team-<team>"
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
      # Tag-scoped reads (resources that support aws:ResourceTag)
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
          "ssm:DescribeParameters",
          "ssm:GetParametersByPath",
          "cloudwatch:DescribeAlarms",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/team" = "$${aws:PrincipalTag/team}"
          }
        }
      },
      # ARN-scoped reads (resources that don't support tag-based IAM conditions)
      {
        Sid    = "ReadOwnTeamLogs"
        Effect = "Allow"
        Action = [
          "logs:GetLogEvents",
          "logs:FilterLogEvents",
          "logs:DescribeLogStreams",
        ]
        Resource = "arn:aws:logs:${var.region}:${var.aws_account_id}:log-group:/ecs/$${aws:PrincipalTag/team}/*"
      },
      {
        Sid      = "DescribeLogGroups"
        Effect   = "Allow"
        Action   = ["logs:DescribeLogGroups"]
        Resource = "*"
      },
      {
        Sid    = "ReadMetrics"
        Effect = "Allow"
        Action = [
          "cloudwatch:GetMetricData",
          "cloudwatch:ListMetrics",
        ]
        Resource = "*"
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
        Resource = "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/apps/$${aws:PrincipalTag/team}/*"
      },
      # Secret values: app.yaml `resources.secrets` creates the parameter with
      # a PLACEHOLDER value; the team sets the real value (`javabin secret
      # set`). Only under the team's own path.
      {
        Sid    = "SetOwnTeamSecrets"
        Effect = "Allow"
        Action = [
          "ssm:PutParameter",
          "ssm:LabelParameterVersion",
          "ssm:GetParameterHistory",
        ]
        Resource = "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/apps/$${aws:PrincipalTag/team}/*"
        # Only parameters the platform created and tagged for this team:
        # creating one by hand would make the app's next terraform apply
        # fail with ParameterAlreadyExists.
        Condition = {
          StringEquals = {
            "aws:ResourceTag/team" = "$${aws:PrincipalTag/team}"
          }
        }
      },
      # Platform-managed parameters in the team path: Cognito credentials and
      # auth-status (app-auth-sync), the auth request (app Terraform) and RDS
      # master passwords (the database keeps the original; changing it breaks the app).
      {
        Sid    = "DenyPlatformManagedParameters"
        Effect = "Deny"
        Action = ["ssm:PutParameter", "ssm:LabelParameterVersion"]
        Resource = [
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/apps/*/*/cognito-client-*",
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/apps/*/*/auth",
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/apps/*/*/auth-status",
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/apps/*/*/db-master-password",
        ]
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
# Managed by the team-provisioner Lambda (CI-triggered from registry).
# Groups are synced and permission sets assigned dynamically — no static
# Terraform resources needed here.
################################################################################
