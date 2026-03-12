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
# The javabin-developer-boundary policy is defined in boundary.tf (written by
# Agent A). We reference it here so all CI roles can attach it. The resource
# aws_iam_policy.developer_boundary must exist in the same module.
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
  permissions_boundary = aws_iam_policy.developer_boundary.arn

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
        Sid    = "BedrockForReview"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:Converse",
        ]
        Resource = "arn:aws:bedrock:${var.region}:${var.aws_account_id}:inference-profile/eu.anthropic.*"
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
  permissions_boundary = aws_iam_policy.developer_boundary.arn

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
            # Main branch ONLY — apply never runs on PRs
            "token.actions.githubusercontent.com:sub" = "repo:${var.github_org}/platform:ref:refs/heads/main"
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
# 2. javabin-ci-app-{repo} — Per-app Terraform role (plan + apply)
#
# Trust: GitHub OIDC with TWO conditions:
#   - sub matches the repo (any ref, since plan runs on PRs too)
#   - job_workflow_ref pins to the platform's tf-plan.yml on main
#
# This means: only workflows defined in javaBin/platform can assume this role,
# even though the workflow runs in the app repo's context. App repos cannot
# write their own workflows to assume this role.
#
# Permissions: Allow *:* with tag-based isolation. The StringEqualsIfExists
# condition ensures apps can only touch resources tagged with their project.
################################################################################

resource "aws_iam_role" "ci_app" {
  for_each = toset(var.registered_app_repos)

  name                 = "${var.project}-ci-app-${each.key}"
  permissions_boundary = aws_iam_policy.developer_boundary.arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AllowPlanAndReviewViaOIDC"
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
            "token.actions.githubusercontent.com:sub" = "repo:${var.github_org}/${each.key}:*"
            # Plan + review (merged into tf-plan) — apply goes through the gate Lambda
            "token.actions.githubusercontent.com:job_workflow_ref" = [
              "${var.github_org}/platform/.github/workflows/tf-plan.yml@refs/heads/main",
            ]
          }
        }
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
    Name = "${var.project}-ci-app-${each.key}"
  }
}

resource "aws_iam_role_policy" "ci_app_allow" {
  for_each = toset(var.registered_app_repos)

  name = "app-management"
  role = aws_iam_role.ci_app[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "AllowWithTagIsolation"
      Effect   = "Allow"
      Action   = "*"
      Resource = "*"
      Condition = {
        StringEqualsIfExists = {
          "aws:ResourceTag/project" = "$${aws:PrincipalTag/project}"
          "aws:RequestTag/project"  = "$${aws:PrincipalTag/project}"
        }
      }
    }]
  })
}

resource "aws_iam_role_policy" "ci_app_deny" {
  for_each = toset(var.registered_app_repos)

  name = "deny-platform-operations"
  role = aws_iam_role.ci_app[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
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
          "ec2:CreateSecurityGroup", "ec2:DeleteSecurityGroup",
          "ec2:AuthorizeSecurityGroupIngress", "ec2:RevokeSecurityGroupIngress",
          "ec2:AuthorizeSecurityGroupEgress", "ec2:RevokeSecurityGroupEgress",
        ]
        Resource = "*"
      },
      {
        Sid    = "DenyLoadBalancerAndCluster"
        Effect = "Deny"
        Action = [
          "elasticloadbalancingv2:CreateLoadBalancer",
          "elasticloadbalancingv2:DeleteLoadBalancer",
          "ecs:CreateCluster", "ecs:DeleteCluster",
        ]
        Resource = "*"
      },
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
      {
        Sid    = "DenyOrgAndAccount"
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
        Sid    = "DenyPlatformSNS"
        Effect = "Deny"
        Action = [
          "sns:CreateTopic",
          "sns:DeleteTopic",
        ]
        Resource = "*"
      },
      {
        Sid      = "DenyStateBucketDeletion"
        Effect   = "Deny"
        Action   = "s3:DeleteBucket"
        Resource = "*"
      },
    ]
  })
}

################################################################################
# 3. javabin-ci-deploy-{repo} — Per-app deploy role (build + push + deploy)
#
# Trust: GitHub OIDC pinned to docker-build and ecs-deploy workflows on main.
# Terraform operations use the app role instead.
# Permissions: Scoped to ECR push, ECS update, CloudWatch logs, SSM read.
################################################################################

resource "aws_iam_role" "ci_deploy" {
  for_each = toset(var.registered_app_repos)

  name                 = "${var.project}-ci-deploy-${each.key}"
  permissions_boundary = aws_iam_policy.developer_boundary.arn

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
            "token.actions.githubusercontent.com:sub" = "repo:${var.github_org}/${each.key}:*"
            # Deployment operations only — Terraform uses the app role
            "token.actions.githubusercontent.com:job_workflow_ref" = [
              "${var.github_org}/platform/.github/workflows/docker-build.yml@refs/heads/main",
              "${var.github_org}/platform/.github/workflows/ecs-deploy.yml@refs/heads/main",
            ]
          }
        }
      }
    ]
  })

  tags = {
    Name = "${var.project}-ci-deploy-${each.key}"
  }
}

resource "aws_iam_role_policy" "ci_deploy_ecr" {
  for_each = toset(var.registered_app_repos)

  name = "ecr-push"
  role = aws_iam_role.ci_deploy[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "EcrAuth"
        Effect = "Allow"
        Action = [
          "ecr:GetAuthorizationToken",
        ]
        # GetAuthorizationToken does not support resource-level permissions
        Resource = "*"
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
        ]
        Resource = "arn:aws:ecr:${var.region}:${var.aws_account_id}:repository/${each.key}"
      }
    ]
  })
}

resource "aws_iam_role_policy" "ci_deploy_ecs" {
  for_each = toset(var.registered_app_repos)

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
            "aws:ResourceTag/project" = var.project
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
        Action = [
          "iam:PassRole",
        ]
        Resource = [
          aws_iam_role.ecs_execution.arn,
          "arn:aws:iam::${var.aws_account_id}:role/${var.project}-*",
        ]
      }
    ]
  })
}

resource "aws_iam_role_policy" "ci_deploy_logs" {
  for_each = toset(var.registered_app_repos)

  name = "cloudwatch-logs"
  role = aws_iam_role.ci_deploy[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogGroup",
          "logs:CreateLogStream",
          "logs:PutLogEvents",
        ]
        Resource = "arn:aws:logs:${var.region}:${var.aws_account_id}:log-group:/ecs/${var.project}/*"
      }
    ]
  })
}

resource "aws_iam_role_policy" "ci_deploy_ssm" {
  for_each = toset(var.registered_app_repos)

  name = "ssm-read-overrides"
  role = aws_iam_role.ci_deploy[each.key].id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = [
          "ssm:GetParameter",
          "ssm:GetParameters",
        ]
        Resource = "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/platform-overrides/*"
      }
    ]
  })
}

################################################################################
# 4. javabin-ci-override-approver — Risk gate override
#
# Trust: GitHub OIDC pinned to approve-override.yml workflow on main, AND
# the GitHub actor must be in the override_approvers list.
# Permissions: ONLY ssm:PutParameter on /javabin/platform-overrides/*
################################################################################

resource "aws_iam_role" "ci_override_approver" {
  name                 = "${var.project}-ci-override-approver"
  permissions_boundary = aws_iam_policy.developer_boundary.arn

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
            "token.actions.githubusercontent.com:aud"              = "sts.amazonaws.com"
            "token.actions.githubusercontent.com:job_workflow_ref" = "${var.github_org}/platform/.github/workflows/approve-override.yml@refs/heads/main"
          }
          StringLike = {
            # Any repo in the org — the workflow_ref + actor conditions are the real gates
            "token.actions.githubusercontent.com:sub" = "repo:${var.github_org}/*:*"
          }
          "ForAnyValue:StringEquals" = {
            "token.actions.githubusercontent.com:actor" = var.override_approvers
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
  permissions_boundary = aws_iam_policy.developer_boundary.arn

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
  permissions_boundary = aws_iam_policy.developer_boundary.arn

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
        Effect = "Allow"
        Action = [
          "secretsmanager:GetSecretValue",
        ]
        Resource = "arn:aws:secretsmanager:${var.region}:${var.aws_account_id}:secret:${var.project}/*"
      }
    ]
  })
}
