################################################################################
# GitHub OIDC Provider (DATA SOURCE — cannot create)
#
# A provider for token.actions.githubusercontent.com already exists in this
# account (managed by a CloudFormation stack). AWS allows only ONE provider
# per issuer URL, so we reference it via data source. Our CI roles attach
# trust policies to this same provider with different conditions.
################################################################################

data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}

################################################################################
# Permission Boundary Reference
#
# The boundary policy lives in terraform/org/boundary.tf (human-applied)
# because the boundary's self-protection prevents CI from modifying it.
# We read it via data source in boundary.tf.
################################################################################

################################################################################
# 1a. javabin-ci-infra-plan — Platform repo plan + review (read-only)
#
# Trust: GitHub OIDC, repo:javaBin/platform on ANY ref (PRs need plan too).
# Permissions: ReadOnlyAccess + specific writes for plan operations:
#   - S3: upload plan artifacts
#   - DynamoDB: Terraform state locking
#   - Bedrock: LLM plan review (inline in plan job)
#   - SSM: read Slack webhook for HIGH risk alerts
#
# This role CANNOT create, modify, or delete infrastructure.
################################################################################

resource "aws_iam_role" "ci_infra_plan" {
  name                 = "${var.project}-ci-infra-plan"
  permissions_boundary = local.org_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = data.aws_iam_openid_connect_provider.github.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            # Any ref — PRs and main both need plan
            "token.actions.githubusercontent.com:sub" = "repo:${var.github_org}/platform:*"
          }
        }
      }
    ]
  })

  tags = {
    Name = "${var.project}-ci-infra-plan"
  }
}

resource "aws_iam_role_policy_attachment" "ci_infra_plan_readonly" {
  role       = aws_iam_role.ci_infra_plan.name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy" "ci_infra_plan_extras" {
  name = "plan-specific-writes"
  role = aws_iam_role.ci_infra_plan.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "TerraformStateLocking"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
        ]
        Resource = "arn:aws:dynamodb:${var.region}:${var.aws_account_id}:table/${var.project}-terraform-infra-lock"
      },
      {
        Sid    = "UploadPlanArtifacts"
        Effect = "Allow"
        Action = [
          "s3:PutObject",
        ]
        Resource = "arn:aws:s3:::${var.project}-ci-plan-artifacts-${var.aws_account_id}/*"
      },
      {
        Sid    = "BedrockInferenceProfile"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:Converse",
        ]
        Resource = "arn:aws:bedrock:${var.region}:${var.aws_account_id}:inference-profile/eu.anthropic.*"
      },
      {
        # Cross-region inference profiles route to foundation models in EU
        # destination regions. IAM requires access to destination model ARNs.
        # Condition scopes this to only work via our inference profiles.
        Sid    = "BedrockFoundationModels"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:Converse",
        ]
        Resource = "arn:aws:bedrock:eu-*::foundation-model/anthropic.*"
        Condition = {
          StringLike = {
            "bedrock:InferenceProfileArn" = "arn:aws:bedrock:${var.region}:${var.aws_account_id}:inference-profile/eu.anthropic.*"
          }
        }
      },
      {
        Sid      = "SSMReadSlackWebhook"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/slack/*"
      },
      {
        # Pricing API for cost estimation during review
        Sid      = "PricingRead"
        Effect   = "Allow"
        Action   = "pricing:GetProducts"
        Resource = "*"
      },
      {
        # BCM Data Exports (CUR 2.0) — not in ReadOnlyAccess managed policy
        Sid    = "BCMDataExportsRead"
        Effect = "Allow"
        Action = [
          "bcm-data-exports:Get*",
          "bcm-data-exports:List*",
        ]
        Resource = "*"
      },
    ]
  })
}

################################################################################
# 1b. javabin-ci-infra — Platform repo apply only
#
# Trust: GitHub OIDC, ONLY platform-ci.yml on main (job_workflow_ref pin).
# Permissions: Allow *:* with permission boundary + explicit deny on operations
# that should never happen via CI (organizations, IAM users, destructive security ops).
################################################################################

resource "aws_iam_role" "ci_infra" {
  name                 = "${var.project}-ci-infra"
  permissions_boundary = local.org_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = data.aws_iam_openid_connect_provider.github.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            # The apply job uses `environment: production`, which changes the
            # OIDC sub claim from ref-based to environment-based. That sub
            # carries no branch, so on its own any branch that declares the
            # environment would match.
            "token.actions.githubusercontent.com:sub" = [
              "repo:${var.github_org}/platform:ref:refs/heads/main",
              "repo:${var.github_org}/platform:environment:production",
            ]
            # The job_workflow_ref pin is what ties this role to main: only
            # platform-ci.yml as committed on main can assume it, whatever
            # the environment's branch policy says.
            "token.actions.githubusercontent.com:job_workflow_ref" = "${var.github_org}/platform/.github/workflows/platform-ci.yml@refs/heads/main"
          }
        }
      }
    ]
  })

  tags = {
    Name = "${var.project}-ci-infra"
  }
}

resource "aws_iam_role_policy" "ci_infra_allow" {
  name = "infra-management"
  role = aws_iam_role.ci_infra.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "AllowAll"
      Effect   = "Allow"
      Action   = "*"
      Resource = "*"
    }]
  })
}

resource "aws_iam_role_policy" "ci_infra_deny" {
  name = "deny-dangerous-operations"
  role = aws_iam_role.ci_infra.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "DenyOrganizationsAndAccount"
        Effect = "Deny"
        Action = [
          "organizations:*",
          "account:*",
        ]
        Resource = "*"
      },
      {
        Sid    = "DenyDangerousIAM"
        Effect = "Deny"
        Action = [
          "iam:CreateUser",
          "iam:CreateAccessKey",
          "iam:CreateLoginProfile",
        ]
        Resource = "*"
      },
      {
        Sid    = "DenyIdentityCenter"
        Effect = "Deny"
        Action = [
          "sso:*",
          "sso-admin:*",
          "identitystore:*",
        ]
        Resource = "*"
      },
      {
        Sid    = "DenySecurityTampering"
        Effect = "Deny"
        Action = [
          "cloudtrail:DeleteTrail",
          "cloudtrail:StopLogging",
          "cloudtrail:UpdateTrail",
          "guardduty:DeleteDetector",
          "securityhub:DisableSecurityHub",
          "config:DeleteConfigurationRecorder",
          "config:StopConfigurationRecorder",
        ]
        Resource = "*"
      },
      {
        Sid      = "DenyStateBucketDeletion"
        Effect   = "Deny"
        Action   = "s3:DeleteBucket"
        Resource = "*"
      }
    ]
  })
}

################################################################################
# 1d. javabin-ci-app-broker — Lightweight OIDC role for invoking broker Lambda
#
# Trust: GitHub OIDC, any org repo, pinned to platform workflow refs.
# Permissions: ONLY invoke the ci-broker Lambda. No AWS resource access.
# The broker Lambda validates team membership and returns scoped credentials.
################################################################################

resource "aws_iam_role" "ci_app_broker" {
  name                 = "${var.project}-ci-app-broker"
  permissions_boundary = aws_iam_policy.developer_boundary.arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = data.aws_iam_openid_connect_provider.github.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
        }
        StringLike = {
          "token.actions.githubusercontent.com:sub" = [
            "repo:${var.github_org}/*:*",
            "repo:${var.github_org}@${var.github_org_id}/*:*",
          ]
          "token.actions.githubusercontent.com:job_workflow_ref" = [
            "${var.github_org}/platform/.github/workflows/tf-plan.yml@refs/heads/main",
            "${var.github_org}/platform/.github/workflows/docker-build.yml@refs/heads/main",
            "${var.github_org}/platform/.github/workflows/ecs-deploy.yml@refs/heads/main",
          ]
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "ci_app_broker" {
  name = "invoke-ci-broker-only"
  role = aws_iam_role.ci_app_broker.id

  # The broker vends team credentials; plan-review reviews app plans with
  # platform credentials (team roles have no Bedrock access).
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = [var.ci_broker_function_arn, var.plan_review_function_arn]
    }]
  })
}

################################################################################
# 2. javabin-ci-team-{team} — Per-team Terraform role (apply)
#
# Trust: ONLY the CI broker Lambda and apply-gate Lambda can assume these roles.
# The broker vends javabin-ci-plan-{team} for plan (2b); the broker trust here
# stays so a broker Lambda still on the old code keeps working during rollout.
# No direct OIDC trust — the broker validates repo→team membership via GitHub
# API before issuing credentials. This is the hard enforcement point.
#
# Permissions: Allow *:* with team-based tag isolation. Resources must be
# tagged with the correct team name.
################################################################################

locals {
  state_bucket_arn = "arn:aws:s3:::${var.project}-terraform-state-${var.aws_account_id}"

  # State keys a team's CI roles must not touch: the platform's own state
  # (infra/ holds the Google OAuth client secret) and every other team's
  # apps/{team}/ prefix. A Deny with NotResource would also hit every other
  # S3 object in the account, so the forbidden prefixes are listed instead.
  # The boundary's DenyXTeam exempts the state bucket, and AllowAll /
  # ReadOnlyAccess cover every key, so without this Deny a team role could
  # read (ci_plan) or overwrite (ci_team) any state in the bucket.
  foreign_state_keys = {
    for team in var.registered_teams : team => concat(
      [for prefix in ["infra", "org", "state", "env:"] : "${local.state_bucket_arn}/${prefix}/*"],
      [for other in var.registered_teams : "${local.state_bucket_arn}/apps/${other}/*" if other != team],
    )
  }
}

resource "aws_iam_role" "ci_team" {
  for_each = toset(var.registered_teams)

  name                 = "${var.project}-ci-team-${each.key}"
  permissions_boundary = aws_iam_policy.developer_boundary.arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowViaCIBroker"
        Effect = "Allow"
        Principal = {
          AWS = var.ci_broker_role_arn
        }
        Action = "sts:AssumeRole"
      },
      {
        Sid    = "AllowApplyViaGateLambda"
        Effect = "Allow"
        Principal = {
          AWS = var.apply_gate_role_arn
        }
        Action = "sts:AssumeRole"
      },
    ]
  })

  tags = {
    Name = "${var.project}-ci-team-${each.key}"
    team = each.key
  }
}

resource "aws_iam_role_policy" "ci_team_allow" {
  for_each = toset(var.registered_teams)

  name = "team-management"
  role = aws_iam_role.ci_team[each.key].id

  # AllowAll + backend access only. All deny rules are in the developer
  # boundary (iam/boundary.tf) using ABAC variables — one policy for all teams.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AllowAll"
        Effect   = "Allow"
        Action   = "*"
        Resource = "*"
      },
      {
        # Review verdicts and overrides come only from the plan-review and
        # apply-gate Lambdas; a plan job must not mark its own plan LOW.
        Sid    = "DenyWriteReviewVerdicts"
        Effect = "Deny"
        Action = ["s3:PutObject", "s3:DeleteObject"]
        Resource = [
          "arn:aws:s3:::${var.project}-ci-plan-artifacts-${var.aws_account_id}/*/risk.json",
          "arn:aws:s3:::${var.project}-ci-plan-artifacts-${var.aws_account_id}/*/review.json",
          "arn:aws:s3:::${var.project}-ci-plan-artifacts-${var.aws_account_id}/*/override.json",
        ]
      },
      {
        # Terraform state: S3 scoped to team's key prefix.
        Sid    = "AllowTerraformBackend"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
        ]
        Resource = [
          "arn:aws:s3:::${var.project}-terraform-state-${var.aws_account_id}",
          "arn:aws:s3:::${var.project}-terraform-state-${var.aws_account_id}/apps/${each.key}/*",
        ]
      },
      {
        Sid      = "DenyForeignTerraformState"
        Effect   = "Deny"
        Action   = ["s3:GetObject", "s3:GetObjectVersion", "s3:PutObject", "s3:DeleteObject", "s3:DeleteObjectVersion"]
        Resource = local.foreign_state_keys[each.key]
      },
      {
        # Terraform locking: DynamoDB scoped to team's state paths.
        Sid    = "AllowTerraformLocking"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
        ]
        Resource = "arn:aws:dynamodb:${var.region}:${var.aws_account_id}:table/${var.project}-terraform-app-locks"
        Condition = {
          "ForAllValues:StringLike" = {
            "dynamodb:LeadingKeys" = "${var.project}-terraform-state-${var.aws_account_id}/apps/${each.key}/*"
          }
        }
      },
    ]
  })
}

################################################################################
# 2b. javabin-ci-plan-{team} — Per-team read-only Terraform plan role
#
# Trust: ONLY the CI broker Lambda. The broker vends this role for tf-plan,
# which runs on any branch and on pull requests. Write access to the team's
# resources comes only from javabin-ci-team-{team}, which the apply gate
# vends for a reviewed plan.
#
# Permissions: ReadOnlyAccess (refresh + data sources), the team's state
# (read), the state lock, and the plan upload. Named ci-plan-*, not
# ci-team-plan-*, so it cannot collide with a team called "plan-...".
################################################################################

resource "aws_iam_role" "ci_plan" {
  for_each = toset(var.registered_teams)

  name                 = "${var.project}-ci-plan-${each.key}"
  permissions_boundary = aws_iam_policy.developer_boundary.arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowViaCIBroker"
        Effect = "Allow"
        Principal = {
          AWS = var.ci_broker_role_arn
        }
        Action = "sts:AssumeRole"
      },
    ]
  })

  tags = {
    Name = "${var.project}-ci-plan-${each.key}"
    team = each.key
  }
}

resource "aws_iam_role_policy_attachment" "ci_plan_readonly" {
  for_each = toset(var.registered_teams)

  role       = aws_iam_role.ci_plan[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/ReadOnlyAccess"
}

resource "aws_iam_role_policy" "ci_plan" {
  for_each = toset(var.registered_teams)

  name = "plan-backend"
  role = aws_iam_role.ci_plan[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        # Terraform state: read-only. Plan never writes state. The Deny
        # below is what keeps plan off other keys.
        Sid    = "ReadTerraformState"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          "arn:aws:s3:::${var.project}-terraform-state-${var.aws_account_id}",
          "arn:aws:s3:::${var.project}-terraform-state-${var.aws_account_id}/apps/${each.key}/*",
        ]
      },
      {
        # ReadOnlyAccess grants s3:Get* on every key, so the Allow above does
        # not limit reads by itself. See local.foreign_state_keys.
        Sid      = "DenyForeignTerraformState"
        Effect   = "Deny"
        Action   = ["s3:GetObject", "s3:GetObjectVersion"]
        Resource = local.foreign_state_keys[each.key]
      },
      {
        # Plan takes the state lock. Same scoping as ci_team.
        Sid    = "AllowTerraformLocking"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
          "dynamodb:DeleteItem",
        ]
        Resource = "arn:aws:dynamodb:${var.region}:${var.aws_account_id}:table/${var.project}-terraform-app-locks"
        Condition = {
          "ForAllValues:StringLike" = {
            "dynamodb:LeadingKeys" = "${var.project}-terraform-state-${var.aws_account_id}/apps/${each.key}/*"
          }
        }
      },
      {
        # Refreshing aws_secretsmanager_secret_version reads the value;
        # ReadOnlyAccess does not include it. Team-prefixed secrets only.
        Sid      = "RefreshTeamSecrets"
        Effect   = "Allow"
        Action   = "secretsmanager:GetSecretValue"
        Resource = "arn:aws:secretsmanager:${var.region}:${var.aws_account_id}:secret:${each.key}-*"
      },
      {
        # SecureString / secret refresh decrypts through the owning service.
        Sid      = "DecryptViaParameterServices"
        Effect   = "Allow"
        Action   = "kms:Decrypt"
        Resource = "*" # Key ARNs are not known here; scoped by kms:ViaService
        Condition = {
          StringEquals = {
            "kms:ViaService" = [
              "ssm.${var.region}.amazonaws.com",
              "secretsmanager.${var.region}.amazonaws.com",
            ]
          }
        }
      },
      {
        # upload-plan.sh stores tfplan + plan-output.txt for the apply gate.
        Sid      = "UploadPlan"
        Effect   = "Allow"
        Action   = "s3:PutObject"
        Resource = "arn:aws:s3:::${var.project}-ci-plan-artifacts-${var.aws_account_id}/plans/*"
      },
      {
        # Review verdicts and overrides come only from the plan-review and
        # apply-gate Lambdas; a plan job must not mark its own plan LOW.
        Sid    = "DenyWriteReviewVerdicts"
        Effect = "Deny"
        Action = ["s3:PutObject", "s3:DeleteObject"]
        Resource = [
          "arn:aws:s3:::${var.project}-ci-plan-artifacts-${var.aws_account_id}/*/risk.json",
          "arn:aws:s3:::${var.project}-ci-plan-artifacts-${var.aws_account_id}/*/review.json",
          "arn:aws:s3:::${var.project}-ci-plan-artifacts-${var.aws_account_id}/*/override.json",
        ]
      },
    ]
  })
}

################################################################################
# 3. javabin-ci-deploy-{team} — Per-team deploy role (build + push + deploy)
#
# Trust: ONLY the CI broker Lambda can assume these roles.
# Same enforcement model as team roles — broker validates team membership.
# Permissions: Scoped to ECR push, ECS update, CloudWatch logs, SSM read.
################################################################################

resource "aws_iam_role" "ci_deploy" {
  for_each = toset(var.registered_teams)

  name                 = "${var.project}-ci-deploy-${each.key}"
  permissions_boundary = aws_iam_policy.developer_boundary.arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowViaCIBroker"
        Effect = "Allow"
        Principal = {
          AWS = var.ci_broker_role_arn
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name = "${var.project}-ci-deploy-${each.key}"
    team = each.key
  }
}

resource "aws_iam_role_policy" "ci_deploy_ecr" {
  for_each = toset(var.registered_teams)

  name = "ecr-push"
  role = aws_iam_role.ci_deploy[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "EcrAuth"
        Effect   = "Allow"
        Action   = ["ecr:GetAuthorizationToken"]
        Resource = "*" # GetAuthorizationToken does not support resource-level permissions
      },
      {
        Sid    = "EcrPush"
        Effect = "Allow"
        Action = [
          "ecr:BatchCheckLayerAvailability",
          "ecr:PutImage",
          "ecr:InitiateLayerUpload",
          "ecr:UploadLayerPart",
          "ecr:CompleteLayerUpload",
          "ecr:GetDownloadUrlForLayer",
          "ecr:BatchGetImage",
          "ecr:CreateRepository",
          "ecr:DescribeRepositories",
        ]
        # Scoped to team's repos. ECR repos use {team}-{service} naming.
        Resource = "arn:aws:ecr:${var.region}:${var.aws_account_id}:repository/${each.key}-*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "ci_deploy_ecs" {
  for_each = toset(var.registered_teams)

  name = "ecs-deploy"
  role = aws_iam_role.ci_deploy[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EcsServiceManagement"
        Effect = "Allow"
        Action = [
          "ecs:UpdateService",
          "ecs:DescribeServices",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "aws:ResourceTag/team" = each.key
          }
        }
      },
      {
        # Task definitions don't support tag-based authorization
        Sid    = "EcsTaskDefinitionManagement"
        Effect = "Allow"
        Action = [
          "ecs:DescribeTaskDefinition",
          "ecs:RegisterTaskDefinition",
          "ecs:DeregisterTaskDefinition",
        ]
        Resource = "*"
      },
      {
        Sid    = "PassRole"
        Effect = "Allow"
        Action = ["iam:PassRole"]
        Resource = [
          aws_iam_role.ecs_exec_team[each.key].arn,
          "arn:aws:iam::${var.aws_account_id}:role/${each.key}-*",
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy" "ci_deploy_logs" {
  for_each = toset(var.registered_teams)

  name = "cloudwatch-logs"
  role = aws_iam_role.ci_deploy[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Action = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = [
        "arn:aws:logs:${var.region}:${var.aws_account_id}:log-group:/ecs/${each.key}/*",
        "arn:aws:logs:${var.region}:${var.aws_account_id}:log-group:/ecs/${each.key}/*:*",
      ]
    }]
  })
}

resource "aws_iam_role_policy" "ci_deploy_ssm" {
  for_each = toset(var.registered_teams)

  name = "ssm-read-overrides"
  role = aws_iam_role.ci_deploy[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = ["ssm:GetParameter", "ssm:GetParameters"]
      Resource = "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/platform-overrides/*"
    }]
  })
}

################################################################################
# 4. javabin-ci-override-approver — Risk gate override
#
# Trust: GitHub OIDC pinned to approve-override.yml workflow on main.
# Access control is handled by GitHub: only repo admins/CODEOWNERS can trigger
# workflow_dispatch, and the override-approval environment can require reviewers.
# Permissions: ONLY ssm:PutParameter on /javabin/platform-overrides/*
################################################################################

resource "aws_iam_role" "ci_override_approver" {
  name                 = "${var.project}-ci-override-approver"
  permissions_boundary = local.org_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = data.aws_iam_openid_connect_provider.github.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            "token.actions.githubusercontent.com:sub" = [
              "repo:${var.github_org}/*:*",
              "repo:${var.github_org}@${var.github_org_id}/*:*",
            ]
            # Pin to the approve-override workflow on main branch
            "token.actions.githubusercontent.com:job_workflow_ref" = "${var.github_org}/platform/.github/workflows/approve-override.yml@refs/heads/main"
          }
        }
      }
    ]
  })

  tags = {
    Name = "${var.project}-ci-override-approver"
  }
}

resource "aws_iam_role_policy" "ci_override_approver" {
  name = "invoke-apply-gate"
  role = aws_iam_role.ci_override_approver.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = "arn:aws:lambda:${var.region}:${var.aws_account_id}:function:${var.project}-apply-gate"
    }]
  })
}

################################################################################
# 5. javabin-ci-registry — Registry repo team provisioning
#
# Trust: GitHub OIDC pinned to javaBin/registry on main branch.
# Permissions: ONLY lambda:InvokeFunction on the team-provisioner Lambda.
# This lets the registry invoke the Lambda directly on merge, without
# dispatching through the platform repo.
################################################################################

resource "aws_iam_role" "ci_registry" {
  name                 = "${var.project}-ci-registry"
  permissions_boundary = local.org_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = data.aws_iam_openid_connect_provider.github.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            "token.actions.githubusercontent.com:sub" = "repo:${var.github_org}/registry:ref:refs/heads/main"
          }
        }
      }
    ]
  })

  tags = {
    Name = "${var.project}-ci-registry"
  }
}

resource "aws_iam_role_policy" "ci_registry" {
  name = "registry-operations"
  role = aws_iam_role.ci_registry.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvokeTeamProvisioner"
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = "arn:aws:lambda:${var.region}:${var.aws_account_id}:function:${var.project}-team-provisioner"
      },
      {
        Sid    = "SSMReadGoogleCreds"
        Effect = "Allow"
        Action = "ssm:GetParameter"
        Resource = [
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/platform/google-admin-sa",
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/platform/google-admin-email",
        ]
      },
    ]
  })
}

################################################################################
# 6. javabin-ci-apply-gate — Lightweight role for tf-apply
#
# Trust: GitHub OIDC pinned to tf-apply.yml workflow on main.
# Permissions: ONLY invoke the apply-gate Lambda + read plan artifacts from S3.
# The actual infrastructure permissions come from the gate Lambda (via STS).
################################################################################

resource "aws_iam_role" "ci_apply_gate" {
  name                 = "${var.project}-ci-apply-gate"
  permissions_boundary = local.org_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Federated = data.aws_iam_openid_connect_provider.github.arn
        }
        Action = "sts:AssumeRoleWithWebIdentity"
        Condition = {
          StringEquals = {
            "token.actions.githubusercontent.com:aud" = "sts.amazonaws.com"
          }
          StringLike = {
            "token.actions.githubusercontent.com:sub" = [
              "repo:${var.github_org}/*:*",
              "repo:${var.github_org}@${var.github_org_id}/*:*",
            ]
            "token.actions.githubusercontent.com:job_workflow_ref" = "${var.github_org}/platform/.github/workflows/tf-apply.yml@refs/heads/main"
          }
        }
      }
    ]
  })

  tags = {
    Name = "${var.project}-ci-apply-gate"
  }
}

resource "aws_iam_role_policy" "ci_apply_gate" {
  name = "invoke-gate-and-read-plans"
  role = aws_iam_role.ci_apply_gate.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "InvokeGateLambda"
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = "arn:aws:lambda:${var.region}:${var.aws_account_id}:function:${var.project}-apply-gate"
      },
      {
        Sid      = "ReadPlanArtifacts"
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "arn:aws:s3:::${var.project}-ci-plan-artifacts-${var.aws_account_id}/*"
      },
    ]
  })
}

################################################################################
# ECS Execution Role — pulls images, writes logs, reads secrets
#
# This is the shared execution role for all ECS tasks on the platform.
# Individual task roles are created per-app via the service-role module.
################################################################################

# Deprecated shared execution role. Kept (image pull + logs only, no secrets)
# until no task definition references it; teams can no longer pass it.
resource "aws_iam_role" "ecs_execution" {
  name = "${var.project}-ecs-execution"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name = "${var.project}-ecs-execution"
    team = "shared"
  }
}

resource "aws_iam_role_policy_attachment" "ecs_execution_base" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

################################################################################
# Per-team ECS execution roles — javabin-ecs-exec-{team}
#
# The execution role pulls the image and resolves the task's `secrets` at
# launch. One shared role that can read every /<project>/apps/* parameter let
# any team mount another team's secrets in its own task definition. Each team
# now gets a role that reads only its own path, and the developer boundary's
# DenyPassRole only lets a team pass its own role.
#
# Tagged team=shared so DenyShared/DenyTagStrip stop teams from editing them.
################################################################################

resource "aws_iam_role" "ecs_exec_team" {
  for_each = toset(var.registered_teams)

  name = "${var.project}-ecs-exec-${each.key}"
  # The org boundary denies creating any role without one
  permissions_boundary = local.org_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Principal = {
          Service = "ecs-tasks.amazonaws.com"
        }
        Action = "sts:AssumeRole"
      }
    ]
  })

  tags = {
    Name     = "${var.project}-ecs-exec-${each.key}"
    team     = "shared"
    for-team = each.key
  }
}

resource "aws_iam_role_policy_attachment" "ecs_exec_team_base" {
  for_each = toset(var.registered_teams)

  role       = aws_iam_role.ecs_exec_team[each.key].name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_exec_team_secrets" {
  for_each = toset(var.registered_teams)

  name = "team-secrets-read"
  role = aws_iam_role.ecs_exec_team[each.key].id

  # Service-secret params and Cognito client credentials (app-auth-sync)
  # under the team's own path only.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "SSMTeamSecrets"
        Effect   = "Allow"
        Action   = ["ssm:GetParameters"]
        Resource = "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/apps/${each.key}/*"
      }
    ]
  })
}
