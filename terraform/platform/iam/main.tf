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
  permissions_boundary = data.aws_iam_policy.developer_boundary.arn

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
    ]
  })
}

################################################################################
# 1b. javabin-ci-infra — Platform repo apply only
#
# Trust: GitHub OIDC, ONLY main branch (not PRs).
# Permissions: Allow *:* with permission boundary + explicit deny on operations
# that should never happen via CI (organizations, IAM users, destructive security ops).
################################################################################

resource "aws_iam_role" "ci_infra" {
  name                 = "${var.project}-ci-infra"
  permissions_boundary = data.aws_iam_policy.developer_boundary.arn

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
            # Main branch OR production environment — the apply job uses
            # `environment: production`, which changes the OIDC sub claim
            # from ref-based to environment-based.
            "token.actions.githubusercontent.com:sub" = [
              "repo:${var.github_org}/platform:ref:refs/heads/main",
              "repo:${var.github_org}/platform:environment:production",
            ]
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
  permissions_boundary = data.aws_iam_policy.developer_boundary.arn

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
          "token.actions.githubusercontent.com:sub" = "repo:${var.github_org}/*:*"
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

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect   = "Allow"
      Action   = "lambda:InvokeFunction"
      Resource = var.ci_broker_function_arn
    }]
  })
}

################################################################################
# 2. javabin-ci-team-{team} — Per-team Terraform role (plan + apply)
#
# Trust: ONLY the CI broker Lambda and apply-gate Lambda can assume these roles.
# No direct OIDC trust — the broker validates repo→team membership via GitHub
# API before issuing credentials. This is the hard enforcement point.
#
# Permissions: Allow *:* with team-based tag isolation. Resources must be
# tagged with the correct team name.
################################################################################

resource "aws_iam_role" "ci_team" {
  for_each = toset(var.registered_teams)

  name                 = "${var.project}-ci-team-${each.key}"
  permissions_boundary = data.aws_iam_policy.developer_boundary.arn

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

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "AllowWithTeamTagIsolation"
        Effect   = "Allow"
        Action   = "*"
        Resource = "*"
        Condition = {
          StringEqualsIfExists = {
            "aws:ResourceTag/team" = each.key
            "aws:RequestTag/team"  = each.key
          }
        }
      },
      {
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
        # App repos reference shared platform infra via data sources (VPC, ALB,
        # ECS cluster, IAM execution role, SNS topics, Route53 zones, etc.).
        # These are read-only operations that don't modify resources, but the
        # ABAC tag condition blocks them because platform resources have
        # team=platform. Allow describe/get/list without tag conditions.
        Sid    = "AllowPlatformDataSourceReads"
        Effect = "Allow"
        Action = [
          "ec2:Describe*",
          "elasticloadbalancing:Describe*",
          "ecs:DescribeClusters",
          "ecs:ListServices",
          "iam:GetRole",
          "iam:GetPolicy",
          "iam:ListAttachedRolePolicies",
          "sns:GetTopicAttributes",
          "sns:ListTagsForResource",
          "route53:ListHostedZones",
          "route53:GetHostedZone",
          "route53:ListResourceRecordSets",
          "acm:ListCertificates",
          "acm:DescribeCertificate",
          "logs:DescribeLogGroups",
          "logs:CreateLogGroup",
          "logs:PutRetentionPolicy",
          "ecr:GetAuthorizationToken",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_role_policy" "ci_team_deny" {
  for_each = toset(var.registered_teams)

  name = "deny-platform-operations"
  role = aws_iam_role.ci_team[each.key].id

  # Deny policy protects platform infrastructure from team CI roles.
  #
  # Design principle: use tag-based ABAC where AWS supports it, explicit
  # denies only where the service lacks tag condition support. Each statement
  # documents WHY it can't be replaced with ABAC.
  #
  # The allow policy (ci_team_allow) already gates all actions behind
  # aws:ResourceTag/team + aws:RequestTag/team conditions. These denies
  # are a second layer for services that don't honor tag conditions.
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      # ----------------------------------------------------------------
      # EC2 networking — MUST be explicit deny
      #
      # AWS EC2 does NOT support aws:RequestTag conditions on CreateVpc,
      # CreateSubnet, CreateInternetGateway, or CreateNatGateway.
      # Resource doesn't exist at creation time so aws:ResourceTag is N/A.
      # Cross-service dependencies (subnet→VPC) can't be scoped by tag.
      # See: docs.aws.amazon.com/AWSEC2/latest/UserGuide/supported-iam-actions-tagging.html
      # ----------------------------------------------------------------
      {
        Sid    = "DenyNetworkInfra"
        Effect = "Deny"
        Action = [
          "ec2:CreateVpc", "ec2:DeleteVpc", "ec2:ModifyVpcAttribute",
          "ec2:CreateSubnet", "ec2:DeleteSubnet",
          "ec2:CreateNatGateway", "ec2:DeleteNatGateway",
          "ec2:CreateInternetGateway", "ec2:DeleteInternetGateway",
          "ec2:AttachInternetGateway", "ec2:DetachInternetGateway",
          "ec2:CreateRouteTable", "ec2:DeleteRouteTable",
        ]
        Resource = "*"
      },
      # ----------------------------------------------------------------
      # Security services — MUST be explicit deny
      #
      # GuardDuty, Security Hub, AWS Config, and CloudTrail do NOT
      # support aws:ResourceTag or aws:RequestTag conditions in IAM.
      # There is no tag-based mechanism to protect these services.
      # ----------------------------------------------------------------
      {
        Sid    = "DenySecurityServices"
        Effect = "Deny"
        Action = [
          "guardduty:*",
          "securityhub:*",
          "config:*",
          "cloudtrail:*",
        ]
        Resource = "*"
      },
      # ----------------------------------------------------------------
      # Organizations + Account — MUST be explicit deny
      #
      # These are account-level services with no resource tagging.
      # ----------------------------------------------------------------
      {
        Sid    = "DenyOrgAndAccount"
        Effect = "Deny"
        Action = [
          "organizations:*",
          "account:*",
        ]
        Resource = "*"
      },
      # ----------------------------------------------------------------
      # IAM user creation — MUST be explicit deny
      #
      # IAM users are global resources, not taggable per-team.
      # Prevents creation of long-lived credentials.
      # ----------------------------------------------------------------
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
      # ----------------------------------------------------------------
      # Platform SNS topics — scoped to platform ARNs (ABAC-friendly)
      #
      # SNS supports full tag-based ABAC. Teams CAN create their own
      # topics (gated by aws:RequestTag/team in the allow policy).
      # This deny only protects platform-owned alert topics by ARN.
      # ----------------------------------------------------------------
      {
        Sid    = "DenyPlatformSNS"
        Effect = "Deny"
        Action = ["sns:DeleteTopic", "sns:SetTopicAttributes", "sns:Subscribe", "sns:Unsubscribe"]
        Resource = [
          "arn:aws:sns:${var.region}:${var.aws_account_id}:${var.project}-alerts",
          "arn:aws:sns:${var.region}:${var.aws_account_id}:${var.project}-security",
          "arn:aws:sns:${var.region}:${var.aws_account_id}:${var.project}-budget-enforcement",
        ]
      },
      # ----------------------------------------------------------------
      # Platform S3 buckets — scoped to platform ARNs (ABAC-friendly)
      #
      # S3 supports full tag-based ABAC. Teams CAN create/delete their
      # own buckets (gated by aws:RequestTag/team). This protects only
      # the Terraform state and CI artifact buckets.
      # ----------------------------------------------------------------
      {
        Sid    = "DenyPlatformS3"
        Effect = "Deny"
        Action = ["s3:DeleteBucket", "s3:PutBucketPolicy", "s3:DeleteBucketPolicy"]
        Resource = [
          "arn:aws:s3:::${var.project}-terraform-state-${var.aws_account_id}",
          "arn:aws:s3:::${var.project}-ci-plan-artifacts-${var.aws_account_id}",
        ]
      },
      # ----------------------------------------------------------------
      # Platform ECS cluster + ALB — scoped to platform ARNs (ABAC-friendly)
      #
      # ECS and ELBv2 support tag-based ABAC. Teams CAN manage their
      # own services (gated by aws:ResourceTag/team). This protects
      # only the shared platform cluster and load balancer.
      # ----------------------------------------------------------------
      {
        Sid    = "DenyPlatformCompute"
        Effect = "Deny"
        Action = [
          "ecs:DeleteCluster", "ecs:UpdateCluster",
          "elasticloadbalancingv2:DeleteLoadBalancer",
          "elasticloadbalancingv2:ModifyLoadBalancerAttributes",
        ]
        Resource = [
          "arn:aws:ecs:${var.region}:${var.aws_account_id}:cluster/${var.project}-platform",
          "arn:aws:elasticloadbalancingv2:${var.region}:${var.aws_account_id}:loadbalancer/app/${var.project}-*",
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
  permissions_boundary = data.aws_iam_policy.developer_boundary.arn

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
        # ECR push actions don't support tag-based authorization.
        # Blast radius is contained: ci-broker limits which team role you get,
        # and each team can only push to repos they've registered.
        # Wildcard is required because app ECR repos use plain names (not project-prefixed).
        Resource = "arn:aws:ecr:${var.region}:${var.aws_account_id}:repository/*"
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
          aws_iam_role.ecs_execution.arn,
          "arn:aws:iam::${var.aws_account_id}:role/${var.project}-*",
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
      Effect   = "Allow"
      Action   = ["logs:CreateLogGroup", "logs:CreateLogStream", "logs:PutLogEvents"]
      Resource = "arn:aws:logs:${var.region}:${var.aws_account_id}:log-group:/ecs/${var.project}/*"
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
  permissions_boundary = data.aws_iam_policy.developer_boundary.arn

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
            "token.actions.githubusercontent.com:sub" = "repo:${var.github_org}/*:*"
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
  permissions_boundary = data.aws_iam_policy.developer_boundary.arn

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
  permissions_boundary = data.aws_iam_policy.developer_boundary.arn

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
            "token.actions.githubusercontent.com:sub"              = "repo:${var.github_org}/*:*"
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
  }
}

resource "aws_iam_role_policy_attachment" "ecs_execution_base" {
  role       = aws_iam_role.ecs_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

resource "aws_iam_role_policy" "ecs_execution_secrets" {
  name = "secrets-read"
  role = aws_iam_role.ecs_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SSMAppSecrets"
        Effect = "Allow"
        Action = [
          "ssm:GetParameters",
        ]
        Resource = "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/apps/*"
      }
    ]
  })
}
