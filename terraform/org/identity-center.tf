################################################################################
# IAM Identity Center — Permission Sets + Group Assignments
#
# Security-critical: applied manually by admins, never by CI.
# The javabin-ci-infra role has explicit deny on sso:*.
#
# Gated by var.enable_identity_center — requires Google Workspace SAML sync
# to be configured first (groups must exist in Identity Center).
#
# ABAC: The "team" attribute flows from Google group membership through
# SAML assertion → Identity Center → STS session tag. This lets a single
# JavabinDeveloper permission set work for all teams.
################################################################################

data "aws_ssoadmin_instances" "this" {
  count = var.enable_identity_center ? 1 : 0
}

locals {
  instance_arn      = var.enable_identity_center ? tolist(data.aws_ssoadmin_instances.this[0].arns)[0] : ""
  identity_store_id = var.enable_identity_center ? tolist(data.aws_ssoadmin_instances.this[0].identity_store_ids)[0] : ""
}

################################################################################
# Access Control Attributes (ABAC) — map "team" from SAML assertion
################################################################################

resource "aws_ssoadmin_instance_access_control_attributes" "this" {
  count = var.enable_identity_center ? 1 : 0

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
  count = var.enable_identity_center ? 1 : 0

  name             = "${var.project}-admin"
  description      = "Full administrator access for platform infrastructure team"
  instance_arn     = local.instance_arn
  session_duration = var.session_duration
}

resource "aws_ssoadmin_managed_policy_attachment" "admin" {
  count = var.enable_identity_center ? 1 : 0

  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.admin[0].arn
  managed_policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}

################################################################################
# Permission Set: JavabinDeveloper
# ABAC-scoped access for heroes. Uses permission boundary.
# Team tag from SAML assertion enables access to team-tagged resources.
################################################################################

resource "aws_ssoadmin_permission_set" "developer" {
  count = var.enable_identity_center ? 1 : 0

  name             = "${var.project}-developer"
  description      = "Developer access scoped to team resources via ABAC"
  instance_arn     = local.instance_arn
  session_duration = var.session_duration
}

resource "aws_ssoadmin_permissions_boundary_attachment" "developer" {
  count = var.enable_identity_center ? 1 : 0

  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.developer[0].arn

  permissions_boundary {
    customer_managed_policy_reference {
      name = "${var.project}-developer-boundary"
    }
  }
}

resource "aws_ssoadmin_permission_set_inline_policy" "developer" {
  count = var.enable_identity_center ? 1 : 0

  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.developer[0].arn

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
  count = var.enable_identity_center ? 1 : 0

  name             = "${var.project}-readonly"
  description      = "Read-only governance access for board and program committee"
  instance_arn     = local.instance_arn
  session_duration = var.session_duration
}

resource "aws_ssoadmin_managed_policy_attachment" "readonly" {
  count = var.enable_identity_center ? 1 : 0

  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.readonly[0].arn
  managed_policy_arn = "arn:aws:iam::aws:policy/job-function/ViewOnlyAccess"
}

################################################################################
# Identity Center Groups — looked up by display name
# These groups are created by Google SAML sync or SCIM provisioning.
# We reference them by name to assign permission sets.
################################################################################

data "aws_identitystore_group" "infra" {
  count = var.enable_identity_center ? 1 : 0

  identity_store_id = local.identity_store_id

  alternate_identifier {
    unique_attribute {
      attribute_path  = "DisplayName"
      attribute_value = "infra@${var.google_workspace_domain}"
    }
  }
}

data "aws_identitystore_group" "heroes" {
  count = var.enable_identity_center ? 1 : 0

  identity_store_id = local.identity_store_id

  alternate_identifier {
    unique_attribute {
      attribute_path  = "DisplayName"
      attribute_value = "heroes@${var.google_workspace_domain}"
    }
  }
}

data "aws_identitystore_group" "board" {
  count = var.enable_identity_center ? 1 : 0

  identity_store_id = local.identity_store_id

  alternate_identifier {
    unique_attribute {
      attribute_path  = "DisplayName"
      attribute_value = "board@${var.google_workspace_domain}"
    }
  }
}

data "aws_identitystore_group" "pkom" {
  count = var.enable_identity_center ? 1 : 0

  identity_store_id = local.identity_store_id

  alternate_identifier {
    unique_attribute {
      attribute_path  = "DisplayName"
      attribute_value = "pkom@${var.google_workspace_domain}"
    }
  }
}

################################################################################
# Group → Permission Set Assignments
################################################################################

resource "aws_ssoadmin_account_assignment" "admin_infra" {
  count = var.enable_identity_center ? 1 : 0

  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.admin[0].arn

  principal_id   = data.aws_identitystore_group.infra[0].group_id
  principal_type = "GROUP"
  target_id      = var.aws_account_id
  target_type    = "AWS_ACCOUNT"
}

resource "aws_ssoadmin_account_assignment" "developer_heroes" {
  count = var.enable_identity_center ? 1 : 0

  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.developer[0].arn

  principal_id   = data.aws_identitystore_group.heroes[0].group_id
  principal_type = "GROUP"
  target_id      = var.aws_account_id
  target_type    = "AWS_ACCOUNT"
}

resource "aws_ssoadmin_account_assignment" "readonly_board" {
  count = var.enable_identity_center ? 1 : 0

  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.readonly[0].arn

  principal_id   = data.aws_identitystore_group.board[0].group_id
  principal_type = "GROUP"
  target_id      = var.aws_account_id
  target_type    = "AWS_ACCOUNT"
}

resource "aws_ssoadmin_account_assignment" "readonly_pkom" {
  count = var.enable_identity_center ? 1 : 0

  instance_arn       = local.instance_arn
  permission_set_arn = aws_ssoadmin_permission_set.readonly[0].arn

  principal_id   = data.aws_identitystore_group.pkom[0].group_id
  principal_type = "GROUP"
  target_id      = var.aws_account_id
  target_type    = "AWS_ACCOUNT"
}
