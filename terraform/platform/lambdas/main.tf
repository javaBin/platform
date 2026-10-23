################################################################################
# Lambda Functions — slack-alert, cost-report, daily-cost-check,
#                    compliance-reporter, resource-tagger, override-cleanup,
#                    team-provisioner
################################################################################

locals {
  lambda_src_path = "${path.module}/../../lambda-src"
  github_org_url  = "https://github.com/javaBin"

  # EU cross-region inference profiles route to any eu-* region.
  # IAM requires foundation model access in destination regions.
  bedrock_eu_foundation_model_arns = "arn:aws:bedrock:eu-*::foundation-model/anthropic.*"
  bedrock_inference_profile_arn    = "arn:aws:bedrock:${var.region}:${var.aws_account_id}:inference-profile/eu.anthropic.*"
}

################################################################################
# Archive files — build zips from source
################################################################################

# Each Lambda zip includes handler.py + shared/ modules it needs.
# Using explicit source blocks so shared/ is at the right path for imports.

data "archive_file" "slack_alert" {
  type             = "zip"
  output_path      = "${path.module}/builds/slack_alert.zip"
  output_file_mode = "0644"

  source {
    content  = file("${local.lambda_src_path}/slack_alert/handler.py")
    filename = "handler.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/__init__.py")
    filename = "shared/__init__.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/pricing.py")
    filename = "shared/pricing.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/slack.py")
    filename = "shared/slack.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/constants.py")
    filename = "shared/constants.py"
  }
}

data "archive_file" "cost_report" {
  type             = "zip"
  output_path      = "${path.module}/builds/cost_report.zip"
  output_file_mode = "0644"

  source {
    content  = file("${local.lambda_src_path}/cost_report/handler.py")
    filename = "handler.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/__init__.py")
    filename = "shared/__init__.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/slack.py")
    filename = "shared/slack.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/constants.py")
    filename = "shared/constants.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/athena.py")
    filename = "shared/athena.py"
  }
}

data "archive_file" "daily_cost_check" {
  type             = "zip"
  output_path      = "${path.module}/builds/daily_cost_check.zip"
  output_file_mode = "0644"

  source {
    content  = file("${local.lambda_src_path}/daily_cost_check/handler.py")
    filename = "handler.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/__init__.py")
    filename = "shared/__init__.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/slack.py")
    filename = "shared/slack.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/constants.py")
    filename = "shared/constants.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/athena.py")
    filename = "shared/athena.py"
  }
}

data "archive_file" "compliance_reporter" {
  type             = "zip"
  output_path      = "${path.module}/builds/compliance_reporter.zip"
  output_file_mode = "0644"
  source_dir       = "${local.lambda_src_path}/compliance_reporter"
}

data "archive_file" "resource_tagger" {
  type             = "zip"
  output_path      = "${path.module}/builds/resource_tagger.zip"
  output_file_mode = "0644"
  source_dir       = "${local.lambda_src_path}/resource_tagger"
}

data "archive_file" "override_cleanup" {
  type             = "zip"
  output_path      = "${path.module}/builds/override_cleanup.zip"
  output_file_mode = "0644"
  source_dir       = "${local.lambda_src_path}/override_cleanup"
}

data "archive_file" "team_provisioner" {
  type             = "zip"
  output_path      = "${path.module}/builds/team_provisioner.zip"
  output_file_mode = "0644"

  source {
    content  = file("${local.lambda_src_path}/team_provisioner/handler.py")
    filename = "handler.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/__init__.py")
    filename = "shared/__init__.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/slack.py")
    filename = "shared/slack.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/constants.py")
    filename = "shared/constants.py"
  }
  # Logo PNG stored as base64 text in the zip (source.content requires string).
  # Python decodes at cold start. The source PNG lives in the repo for auditability.
  source {
    content  = filebase64("${local.lambda_src_path}/team_provisioner/javabin-logo-white.png")
    filename = "assets/javabin-logo-white.b64"
  }
}

data "archive_file" "password_set" {
  type             = "zip"
  output_path      = "${path.module}/builds/password_set.zip"
  output_file_mode = "0644"

  source {
    content  = file("${local.lambda_src_path}/password_set/handler.py")
    filename = "handler.py"
  }
}

data "archive_file" "budget_enforcer" {
  type             = "zip"
  output_path      = "${path.module}/builds/budget_enforcer.zip"
  output_file_mode = "0644"

  source {
    content  = file("${local.lambda_src_path}/budget_enforcer/handler.py")
    filename = "handler.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/__init__.py")
    filename = "shared/__init__.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/slack.py")
    filename = "shared/slack.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/constants.py")
    filename = "shared/constants.py"
  }
}

################################################################################
# IAM Roles
################################################################################

# --- slack-alert role ---
resource "aws_iam_role" "slack_alert" {
  name = "${var.project}-slack-alert"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "slack_alert" {
  name = "${var.project}-slack-alert"
  role = aws_iam_role.slack_alert.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SSMRead"
        Effect = "Allow"
        Action = "ssm:GetParameter"
        Resource = [
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/javabin/slack/*",
        ]
      },
      {
        Sid      = "BedrockInferenceProfile"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:Converse"]
        Resource = local.bedrock_inference_profile_arn
      },
      {
        Sid      = "BedrockFoundationModels"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:Converse"]
        Resource = local.bedrock_eu_foundation_model_arns
        Condition = {
          StringLike = {
            "bedrock:InferenceProfileArn" = local.bedrock_inference_profile_arn
          }
        }
      },
      {
        Sid    = "PricingRead"
        Effect = "Allow"
        Action = "pricing:GetProducts"
        # Pricing API is global and only supports * for resource
        Resource = "*"
      },
      {
        Sid    = "DynamoDBDedup"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
        ]
        Resource = var.alert_dedup_table_arn
      },
      {
        Sid    = "SecurityHubRead"
        Effect = "Allow"
        Action = "securityhub:GetFindings"
        # Security Hub requires * for resource
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "slack_alert_logs" {
  role       = aws_iam_role.slack_alert.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# --- cost-report role ---
resource "aws_iam_role" "cost_report" {
  name = "${var.project}-cost-report"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "cost_report" {
  name = "${var.project}-cost-report"
  role = aws_iam_role.cost_report.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "SSMRead"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/javabin/slack/*"
      },
      {
        Sid    = "CostExplorer"
        Effect = "Allow"
        Action = [
          "ce:GetCostAndUsage",
        ]
        # Cost Explorer is a global service, requires *
        Resource = "*"
      },
      {
        Sid      = "BedrockInferenceProfile"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:Converse"]
        Resource = local.bedrock_inference_profile_arn
      },
      {
        Sid      = "BedrockFoundationModels"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:Converse"]
        Resource = local.bedrock_eu_foundation_model_arns
        Condition = {
          StringLike = {
            "bedrock:InferenceProfileArn" = local.bedrock_inference_profile_arn
          }
        }
      },
      {
        Sid    = "AthenaQuery"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution",
        ]
        Resource = "arn:aws:athena:${var.region}:${var.aws_account_id}:workgroup/${var.athena_workgroup}"
      },
      {
        Sid    = "GlueReadCatalog"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetTable",
          "glue:GetPartitions",
        ]
        Resource = [
          "arn:aws:glue:${var.region}:${var.aws_account_id}:catalog",
          "arn:aws:glue:${var.region}:${var.aws_account_id}:database/${var.cur_glue_database}",
          "arn:aws:glue:${var.region}:${var.aws_account_id}:table/${var.cur_glue_database}/${var.cur_glue_table}",
        ]
      },
      {
        Sid    = "S3ReadCURData"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          var.cur_data_bucket_arn,
          "${var.cur_data_bucket_arn}/*",
        ]
      },
      {
        Sid    = "S3WriteAthenaResults"
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject", "s3:GetBucketLocation", "s3:AbortMultipartUpload", "s3:ListBucket"]
        Resource = [
          var.athena_results_bucket_arn,
          "${var.athena_results_bucket_arn}/*",
        ]
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "cost_report_logs" {
  role       = aws_iam_role.cost_report.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# --- daily-cost-check role ---
resource "aws_iam_role" "daily_cost_check" {
  name = "${var.project}-daily-cost-check"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "daily_cost_check" {
  name = "${var.project}-daily-cost-check"
  role = aws_iam_role.daily_cost_check.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "SSMRead"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/javabin/slack/*"
      },
      {
        Sid      = "CostExplorer"
        Effect   = "Allow"
        Action   = "ce:GetCostAndUsage"
        Resource = "*"
      },
      {
        Sid    = "AthenaQuery"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution",
        ]
        Resource = "arn:aws:athena:${var.region}:${var.aws_account_id}:workgroup/${var.athena_workgroup}"
      },
      {
        Sid    = "GlueReadCatalog"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetTable",
          "glue:GetPartitions",
        ]
        Resource = [
          "arn:aws:glue:${var.region}:${var.aws_account_id}:catalog",
          "arn:aws:glue:${var.region}:${var.aws_account_id}:database/${var.cur_glue_database}",
          "arn:aws:glue:${var.region}:${var.aws_account_id}:table/${var.cur_glue_database}/${var.cur_glue_table}",
        ]
      },
      {
        Sid    = "S3ReadCURData"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          var.cur_data_bucket_arn,
          "${var.cur_data_bucket_arn}/*",
        ]
      },
      {
        Sid    = "S3WriteAthenaResults"
        Effect = "Allow"
        Action = ["s3:PutObject", "s3:GetObject", "s3:GetBucketLocation", "s3:AbortMultipartUpload", "s3:ListBucket"]
        Resource = [
          var.athena_results_bucket_arn,
          "${var.athena_results_bucket_arn}/*",
        ]
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "daily_cost_check_logs" {
  role       = aws_iam_role.daily_cost_check.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# --- compliance-reporter role ---
resource "aws_iam_role" "compliance_reporter" {
  name = "${var.project}-compliance-reporter"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "compliance_reporter" {
  name = "${var.project}-compliance-reporter"
  role = aws_iam_role.compliance_reporter.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "SSMRead"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/javabin/slack/*"
      },
      {
        Sid    = "ReadTags"
        Effect = "Allow"
        Action = [
          "ec2:DescribeTags",
          "s3:GetBucketTagging",
          "ecs:ListTagsForResource",
          "elasticloadbalancing:DescribeTags",
          "lambda:ListTags",
          "rds:ListTagsForResource",
        ]
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "compliance_reporter_logs" {
  role       = aws_iam_role.compliance_reporter.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# --- resource-tagger role ---
resource "aws_iam_role" "resource_tagger" {
  name                 = "${var.project}-resource-tagger"
  permissions_boundary = var.org_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "resource_tagger" {
  name = "${var.project}-resource-tagger"
  role = aws_iam_role.resource_tagger.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "TagResources"
        Effect = "Allow"
        Action = [
          "tag:TagResources",
          "tag:GetResources",
        ]
        # Resource Groups Tagging API requires resource "*"
        Resource = "*"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "resource_tagger_logs" {
  role       = aws_iam_role.resource_tagger.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# --- override-cleanup role ---
resource "aws_iam_role" "override_cleanup" {
  name = "${var.project}-override-cleanup"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "override_cleanup" {
  name = "${var.project}-override-cleanup"
  role = aws_iam_role.override_cleanup.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SSMOverrides"
        Effect = "Allow"
        Action = [
          "ssm:GetParametersByPath",
          "ssm:DeleteParameter",
        ]
        Resource = "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/javabin/platform-overrides/*"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "override_cleanup_logs" {
  role       = aws_iam_role.override_cleanup.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# --- team-provisioner role ---
resource "aws_iam_role" "team_provisioner" {
  name = "${var.project}-team-provisioner"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "team_provisioner" {
  name = "${var.project}-team-provisioner"
  role = aws_iam_role.team_provisioner.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "WriteAuthMemberships"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:DeleteItem", "dynamodb:Scan"]
        Resource = var.auth_memberships_table_arn
      },
      {
        Sid    = "SSMRead"
        Effect = "Allow"
        Action = "ssm:GetParameter"
        Resource = [
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/javabin/platform/*",
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/javabin/slack/*",
        ]
      },
      {
        Sid    = "BudgetsManage"
        Effect = "Allow"
        Action = [
          "budgets:CreateBudget",
          "budgets:ModifyBudget",
          "budgets:ViewBudget",
        ]
        Resource = "arn:aws:budgets::${var.aws_account_id}:budget/javabin-team-*"
      },
      {
        Sid    = "CognitoGroupSync"
        Effect = "Allow"
        Action = [
          "cognito-idp:CreateGroup",
          "cognito-idp:GetGroup",
          "cognito-idp:UpdateGroup",
          "cognito-idp:ListUsersInGroup",
          "cognito-idp:AdminAddUserToGroup",
          "cognito-idp:AdminRemoveUserFromGroup",
          "cognito-idp:ListUsers",
        ]
        Resource = var.internal_user_pool_arn
      },
      {
        Sid    = "IdentityStoreSync"
        Effect = "Allow"
        Action = [
          "identitystore:CreateGroup",
          "identitystore:DescribeGroup",
          "identitystore:DescribeUser",
          "identitystore:ListGroups",
          "identitystore:CreateGroupMembership",
          "identitystore:ListGroupMemberships",
          "identitystore:DeleteGroupMembership",
          "identitystore:ListUsers",
        ]
        # Identity Store API requires * — store ID scoped via env var
        Resource = "*"
      },
      {
        Sid    = "SSOManagement"
        Effect = "Allow"
        Action = [
          "sso:CreateAccountAssignment",
          "sso:DescribeAccountAssignmentCreationStatus",
          "sso:ListPermissionSets",
          "sso:DescribePermissionSet",
          "sso:CreatePermissionSet",
          "sso:PutInlinePolicyToPermissionSet",
          "sso:AttachCustomerManagedPolicyReferenceToPermissionSet",
        ]
        Resource = "*"
      },
      {
        Sid    = "IAMForSSOAssignment"
        Effect = "Allow"
        Action = [
          "iam:GetSAMLProvider",
          "iam:GetRole",
          "iam:CreateRole",
          "iam:AttachRolePolicy",
          "iam:PutRolePolicy",
          "iam:UpdateRole",
          "iam:UpdateRoleDescription",
          "iam:ListAttachedRolePolicies",
          "iam:ListRolePolicies",
          "iam:DeleteRole",
          "iam:DeleteRolePolicy",
          "iam:DetachRolePolicy",
        ]
        # Scoped to SSO-managed roles and SAML providers
        Resource = [
          "arn:aws:iam::${var.aws_account_id}:role/aws-reserved/sso.amazonaws.com/*",
          "arn:aws:iam::${var.aws_account_id}:saml-provider/AWSSSO_*",
        ]
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "team_provisioner_logs" {
  role       = aws_iam_role.team_provisioner.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

# --- password-set role ---
resource "aws_iam_role" "password_set" {
  name                 = "${var.project}-password-set"
  permissions_boundary = var.org_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "password_set" {
  name = "${var.project}-password-set"
  role = aws_iam_role.password_set.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "SSMRead"
        Effect = "Allow"
        Action = "ssm:GetParameter"
        Resource = [
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/javabin/platform/*",
        ]
      },
      {
        Sid    = "DynamoDBDedup"
        Effect = "Allow"
        Action = [
          "dynamodb:GetItem",
          "dynamodb:PutItem",
        ]
        Resource = var.alert_dedup_table_arn
      },
      {
        Sid      = "InvokeTeamProvisioner"
        Effect   = "Allow"
        Action   = "lambda:InvokeFunction"
        Resource = aws_lambda_function.team_provisioner.arn
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "password_set_logs" {
  role       = aws_iam_role.password_set.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

################################################################################
# Lambda Functions
################################################################################

resource "aws_lambda_function" "slack_alert" {
  function_name    = "${var.project}-slack-alert"
  role             = aws_iam_role.slack_alert.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 256
  filename         = data.archive_file.slack_alert.output_path
  source_code_hash = data.archive_file.slack_alert.output_base64sha256

  environment {
    variables = {
      INFRA_WEBHOOK_PARAM    = "/javabin/slack/platform-resource-alerts-webhook"
      COST_WEBHOOK_PARAM     = "/javabin/slack/platform-cost-alerts-webhook"
      SECURITY_WEBHOOK_PARAM = "/javabin/slack/platform-security-alerts-webhook"
      SECURITY_TOPIC_ARN     = var.security_topic_arn
      PROJECT_PREFIX         = var.project
      GITHUB_ORG_URL         = local.github_org_url
      DEPLOY_REGION          = var.region
      DEDUP_TABLE_NAME       = var.alert_dedup_table_name
    }
  }
}

resource "aws_lambda_function" "cost_report" {
  function_name    = "${var.project}-cost-report"
  role             = aws_iam_role.cost_report.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 120
  memory_size      = 256
  filename         = data.archive_file.cost_report.output_path
  source_code_hash = data.archive_file.cost_report.output_base64sha256

  environment {
    variables = {
      COST_WEBHOOK_PARAM = "/javabin/slack/platform-cost-alerts-webhook"
      DEPLOY_REGION      = var.region
      CUR_DATABASE       = var.cur_glue_database
      CUR_TABLE          = var.cur_glue_table
      ATHENA_WORKGROUP   = var.athena_workgroup
    }
  }
}

resource "aws_lambda_function" "daily_cost_check" {
  function_name    = "${var.project}-daily-cost-check"
  role             = aws_iam_role.daily_cost_check.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 90
  memory_size      = 128
  filename         = data.archive_file.daily_cost_check.output_path
  source_code_hash = data.archive_file.daily_cost_check.output_base64sha256

  environment {
    variables = {
      COST_WEBHOOK_PARAM = "/javabin/slack/platform-cost-alerts-webhook"
      MIN_SPIKE_AMOUNT   = "10.00"
      MIN_SPIKE_DELTA    = "10.00"
      CUR_DATABASE       = var.cur_glue_database
      CUR_TABLE          = var.cur_glue_table
      ATHENA_WORKGROUP   = var.athena_workgroup
    }
  }
}

resource "aws_lambda_function" "compliance_reporter" {
  function_name    = "${var.project}-compliance-reporter"
  role             = aws_iam_role.compliance_reporter.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128
  filename         = data.archive_file.compliance_reporter.output_path
  source_code_hash = data.archive_file.compliance_reporter.output_base64sha256

  environment {
    variables = {
      PROJECT_PREFIX      = var.project
      INFRA_WEBHOOK_PARAM = "/javabin/slack/platform-resource-alerts-webhook"
      ALLOWED_IDENTITIES  = join(",", var.compliance_reporter_identities)
    }
  }
}

resource "aws_lambda_function" "resource_tagger" {
  function_name    = "${var.project}-resource-tagger"
  role             = aws_iam_role.resource_tagger.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128
  filename         = data.archive_file.resource_tagger.output_path
  source_code_hash = data.archive_file.resource_tagger.output_base64sha256

  environment {
    variables = {
      AWS_ACCOUNT_ID = var.aws_account_id
    }
  }
}

resource "aws_lambda_function" "override_cleanup" {
  function_name    = "${var.project}-override-cleanup"
  role             = aws_iam_role.override_cleanup.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128
  filename         = data.archive_file.override_cleanup.output_path
  source_code_hash = data.archive_file.override_cleanup.output_base64sha256

  environment {
    variables = {
      OVERRIDE_PATH = "/javabin/platform-overrides/"
      MAX_AGE_HOURS = "24"
    }
  }
}

resource "aws_lambda_function" "team_provisioner" {
  function_name    = "${var.project}-team-provisioner"
  role             = aws_iam_role.team_provisioner.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 256
  filename         = data.archive_file.team_provisioner.output_path
  source_code_hash = data.archive_file.team_provisioner.output_base64sha256

  environment {
    variables = {
      INFRA_WEBHOOK_PARAM          = "/javabin/slack/platform-resource-alerts-webhook"
      GOOGLE_SA_PARAM              = "/javabin/platform/google-admin-sa"
      GOOGLE_ADMIN_EMAIL_PARAM     = "/javabin/platform/google-admin-email"
      GITHUB_APP_ID_PARAM          = "/javabin/platform/github-app-id"
      GITHUB_APP_KEY_PARAM         = "/javabin/platform/github-app-key"
      ACCOUNT_ID                   = var.aws_account_id
      GITHUB_ORG                   = "javaBin"
      ALERTS_TOPIC_ARN             = var.alerts_topic_arn
      COGNITO_INTERNAL_POOL_ID     = var.internal_user_pool_id
      MEMBERSHIPS_TABLE            = var.auth_memberships_table_name
      IDENTITY_STORE_ID            = var.identity_store_id
      SSO_INSTANCE_ARN             = var.sso_instance_arn
      PROJECT                      = var.project
      SIGNING_KEY_PARAM            = "/javabin/platform/password-token-signing-key"
      PASSWORD_SET_URL_PARAM       = "/javabin/platform/password-set-function-url"
      BUDGET_ENFORCEMENT_TOPIC_ARN = aws_sns_topic.budget_enforcement.arn
    }
  }
}

resource "aws_lambda_function" "password_set" {
  function_name                  = "${var.project}-password-set"
  role                           = aws_iam_role.password_set.arn
  handler                        = "handler.handler"
  runtime                        = "python3.12"
  timeout                        = 30
  memory_size                    = 128
  reserved_concurrent_executions = 5 # Rate limiting — prevents brute force on password-set endpoint
  filename                       = data.archive_file.password_set.output_path
  source_code_hash               = data.archive_file.password_set.output_base64sha256

  environment {
    variables = {
      SIGNING_KEY_PARAM         = "/javabin/platform/password-token-signing-key"
      DEDUP_TABLE_NAME          = var.alert_dedup_table_name
      TEAM_PROVISIONER_FUNCTION = aws_lambda_function.team_provisioner.function_name
    }
  }
}

resource "aws_lambda_function_url" "password_set" {
  function_name      = aws_lambda_function.password_set.function_name
  authorization_type = "NONE"
}

# Public access for function URL — both permissions required since Oct 2025.
# FunctionURLAllowPublicAccess is imported (auto-created by the function URL).
resource "aws_lambda_permission" "password_set_public_url" {
  statement_id           = "FunctionURLAllowPublicAccess"
  action                 = "lambda:InvokeFunctionUrl"
  function_name          = aws_lambda_function.password_set.function_name
  principal              = "*"
  function_url_auth_type = "NONE"
}

resource "aws_lambda_permission" "password_set_public_invoke" {
  statement_id  = "AllowPublicInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.password_set.function_name
  principal     = "*"
}

# Store custom domain URL in SSM so team-provisioner uses the friendly URL
# in welcome emails instead of the raw Lambda Function URL.
resource "aws_ssm_parameter" "password_set_function_url" {
  name  = "/javabin/platform/password-set-function-url"
  type  = "String"
  value = "https://password.${var.domain}"
}

################################################################################
# Password-set — ALB routing via password.javazone.no
################################################################################

resource "aws_lb_target_group" "password_set" {
  name        = "${var.project}-password-set"
  target_type = "lambda"
}

resource "aws_lb_target_group_attachment" "password_set" {
  target_group_arn = aws_lb_target_group.password_set.arn
  target_id        = aws_lambda_function.password_set.arn
  depends_on       = [aws_lambda_permission.password_set_alb]
}

resource "aws_lambda_permission" "password_set_alb" {
  statement_id  = "AllowALBInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.password_set.function_name
  principal     = "elasticloadbalancing.amazonaws.com"
  source_arn    = aws_lb_target_group.password_set.arn
}

resource "aws_lb_listener_rule" "password_set" {
  listener_arn = var.https_listener_arn
  priority     = 10

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.password_set.arn
  }

  condition {
    host_header {
      values = ["password.${var.domain}"]
    }
  }
}

resource "aws_route53_record" "password_set" {
  zone_id = var.route53_zone_id
  name    = "password.${var.domain}"
  type    = "A"

  alias {
    name                   = var.alb_dns_name
    zone_id                = var.alb_zone_id
    evaluate_target_health = true
  }
}

################################################################################
# Budget Enforcer — scales ECS services to zero on 200% budget breach
################################################################################

# Dedicated SNS topic for budget enforcement (200% threshold notifications)
resource "aws_sns_topic" "budget_enforcement" {
  name = "${var.project}-budget-enforcement"
}

# Allow AWS Budgets to publish to this topic
resource "aws_sns_topic_policy" "budget_enforcement" {
  arn = aws_sns_topic.budget_enforcement.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowBudgetsPublish"
        Effect    = "Allow"
        Principal = { Service = "budgets.amazonaws.com" }
        Action    = "SNS:Publish"
        Resource  = aws_sns_topic.budget_enforcement.arn
        Condition = {
          StringEquals = {
            "aws:SourceAccount" = var.aws_account_id
          }
        }
      },
    ]
  })
}

# --- budget-enforcer role ---
resource "aws_iam_role" "budget_enforcer" {
  name                 = "${var.project}-budget-enforcer"
  permissions_boundary = var.org_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "budget_enforcer" {
  name = "${var.project}-budget-enforcer"
  role = aws_iam_role.budget_enforcer.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "SSMRead"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/javabin/slack/*"
      },
      {
        Sid    = "ECSListAndDescribe"
        Effect = "Allow"
        Action = [
          "ecs:ListServices",
          "ecs:DescribeServices",
          "ecs:ListTagsForResource",
        ]
        Resource = "*"
        Condition = {
          StringEquals = {
            "ecs:cluster" = "arn:aws:ecs:${var.region}:${var.aws_account_id}:cluster/${var.project}-platform"
          }
        }
      },
      {
        Sid    = "ECSListServicesUnconstrained"
        Effect = "Allow"
        Action = "ecs:ListServices"
        # ListServices requires * or cluster ARN — condition key not supported for list calls
        Resource = "arn:aws:ecs:${var.region}:${var.aws_account_id}:cluster/${var.project}-platform"
      },
      {
        Sid      = "ECSUpdateService"
        Effect   = "Allow"
        Action   = "ecs:UpdateService"
        Resource = "arn:aws:ecs:${var.region}:${var.aws_account_id}:service/${var.project}-platform/*"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "budget_enforcer_logs" {
  role       = aws_iam_role.budget_enforcer.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "budget_enforcer" {
  function_name    = "${var.project}-budget-enforcer"
  role             = aws_iam_role.budget_enforcer.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 128
  filename         = data.archive_file.budget_enforcer.output_path
  source_code_hash = data.archive_file.budget_enforcer.output_base64sha256

  environment {
    variables = {
      ECS_CLUSTER        = "${var.project}-platform"
      COST_WEBHOOK_PARAM = "/javabin/slack/platform-cost-alerts-webhook"
      BUDGET_NAME_PREFIX = "${var.project}-team-"
    }
  }
}

resource "aws_sns_topic_subscription" "budget_enforcer" {
  topic_arn = aws_sns_topic.budget_enforcement.arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.budget_enforcer.arn
}

resource "aws_lambda_permission" "budget_enforcer_sns" {
  statement_id  = "AllowSNSBudgetEnforcement"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.budget_enforcer.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = aws_sns_topic.budget_enforcement.arn
}

################################################################################
# SNS Subscriptions — slack-alert subscribes to both topics
################################################################################

resource "aws_sns_topic_subscription" "slack_alert_security" {
  topic_arn = var.security_topic_arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.slack_alert.arn
}

resource "aws_sns_topic_subscription" "slack_alert_alerts" {
  topic_arn = var.alerts_topic_arn
  protocol  = "lambda"
  endpoint  = aws_lambda_function.slack_alert.arn
}

resource "aws_lambda_permission" "slack_alert_security" {
  statement_id  = "AllowSNSSecurity"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.slack_alert.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = var.security_topic_arn
}

resource "aws_lambda_permission" "slack_alert_alerts" {
  statement_id  = "AllowSNSAlerts"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.slack_alert.function_name
  principal     = "sns.amazonaws.com"
  source_arn    = var.alerts_topic_arn
}

################################################################################
# EventBridge Schedules
################################################################################

# --- cost-report: Monday 08:00 UTC ---
resource "aws_cloudwatch_event_rule" "cost_report_schedule" {
  name                = "${var.project}-cost-report-schedule"
  description         = "Weekly cost report — Monday 08:00 UTC"
  schedule_expression = "cron(0 8 ? * MON *)"
}

resource "aws_cloudwatch_event_target" "cost_report" {
  rule      = aws_cloudwatch_event_rule.cost_report_schedule.name
  target_id = "invoke-cost-report"
  arn       = aws_lambda_function.cost_report.arn
}

resource "aws_lambda_permission" "cost_report_schedule" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.cost_report.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.cost_report_schedule.arn
}

# --- daily-cost-check: Daily 08:00 UTC ---
resource "aws_cloudwatch_event_rule" "daily_cost_check_schedule" {
  name                = "${var.project}-daily-cost-check-schedule"
  description         = "Daily cost spike check — 08:00 UTC"
  schedule_expression = "cron(0 8 * * ? *)"
}

resource "aws_cloudwatch_event_target" "daily_cost_check" {
  rule      = aws_cloudwatch_event_rule.daily_cost_check_schedule.name
  target_id = "invoke-daily-cost-check"
  arn       = aws_lambda_function.daily_cost_check.arn
}

resource "aws_lambda_permission" "daily_cost_check_schedule" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.daily_cost_check.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.daily_cost_check_schedule.arn
}

# --- compliance-reporter: EventBridge — resource creation events ---
resource "aws_cloudwatch_event_rule" "compliance_reporter_trigger" {
  name        = "${var.project}-compliance-reporter-trigger"
  description = "Trigger compliance reporter on resource creation"

  event_pattern = jsonencode({
    source = [
      "aws.s3", "aws.ec2", "aws.rds", "aws.ecs",
      "aws.elasticloadbalancing", "aws.lambda",
    ]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventName = [
        { "prefix" : "Create" },
        { "prefix" : "Run" },
      ]
    }
  })
}

resource "aws_cloudwatch_event_target" "compliance_reporter" {
  rule      = aws_cloudwatch_event_rule.compliance_reporter_trigger.name
  target_id = "invoke-compliance-reporter"
  arn       = aws_lambda_function.compliance_reporter.arn
}

resource "aws_lambda_permission" "compliance_reporter_eventbridge" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.compliance_reporter.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.compliance_reporter_trigger.arn
}

# --- resource-tagger: EventBridge — tag newly created resources ---
resource "aws_cloudwatch_event_rule" "resource_tagger_trigger" {
  name        = "${var.project}-resource-tagger-trigger"
  description = "Tag newly created resources with creator attribution"

  # Match ALL AWS services — resource_tagger has generic ARN extraction
  # via ResourceGroupsTaggingAPI, so new services are auto-covered.
  # Filtered to Create/Run events only, keeping volume manageable.
  event_pattern = jsonencode({
    source      = [{ "prefix" : "aws." }]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventName = [
        { "prefix" : "Create" },
        { "prefix" : "Run" },
      ]
    }
  })
}

resource "aws_cloudwatch_event_target" "resource_tagger" {
  rule      = aws_cloudwatch_event_rule.resource_tagger_trigger.name
  target_id = "invoke-resource-tagger"
  arn       = aws_lambda_function.resource_tagger.arn
}

resource "aws_lambda_permission" "resource_tagger_eventbridge" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.resource_tagger.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.resource_tagger_trigger.arn
}

# --- override-cleanup: hourly ---
resource "aws_cloudwatch_event_rule" "override_cleanup_schedule" {
  name                = "${var.project}-override-cleanup-schedule"
  description         = "Hourly cleanup of stale SSM override parameters"
  schedule_expression = "rate(1 hour)"
}

resource "aws_cloudwatch_event_target" "override_cleanup" {
  rule      = aws_cloudwatch_event_rule.override_cleanup_schedule.name
  target_id = "invoke-override-cleanup"
  arn       = aws_lambda_function.override_cleanup.arn
}

resource "aws_lambda_permission" "override_cleanup_schedule" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.override_cleanup.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.override_cleanup_schedule.arn
}

# --- team-provisioner: no schedule — triggered via direct Lambda invocation from registry CI ---

# --- securityhub-summary: Monday 08:00 UTC (same as cost report) ---
resource "aws_cloudwatch_event_rule" "securityhub_summary_schedule" {
  name                = "${var.project}-securityhub-summary-schedule"
  description         = "Weekly Security Hub summary — Monday 08:00 UTC"
  schedule_expression = "cron(0 8 ? * MON *)"
}

resource "aws_cloudwatch_event_target" "securityhub_summary" {
  rule      = aws_cloudwatch_event_rule.securityhub_summary_schedule.name
  target_id = "invoke-securityhub-summary"
  arn       = aws_lambda_function.securityhub_summary.arn
}

resource "aws_lambda_permission" "securityhub_summary_schedule" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.securityhub_summary.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.securityhub_summary_schedule.arn
}

################################################################################
# Security Hub Summary — weekly summary Lambda (reuses slack-alert code)
################################################################################

resource "aws_lambda_function" "securityhub_summary" {
  function_name    = "${var.project}-securityhub-summary"
  role             = aws_iam_role.slack_alert.arn
  handler          = "handler.summary_handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.slack_alert.output_path
  source_code_hash = data.archive_file.slack_alert.output_base64sha256

  environment {
    variables = {
      INFRA_WEBHOOK_PARAM    = "/javabin/slack/platform-resource-alerts-webhook"
      COST_WEBHOOK_PARAM     = "/javabin/slack/platform-cost-alerts-webhook"
      SECURITY_WEBHOOK_PARAM = "/javabin/slack/platform-security-alerts-webhook"
      SECURITY_TOPIC_ARN     = var.security_topic_arn
      PROJECT_PREFIX         = var.project
      GITHUB_ORG_URL         = local.github_org_url
      DEPLOY_REGION          = var.region
      DEDUP_TABLE_NAME       = var.alert_dedup_table_name
    }
  }
}

################################################################################
# Apply Gate — credential broker for Terraform apply
################################################################################

data "archive_file" "apply_gate" {
  type             = "zip"
  output_path      = "${path.module}/builds/apply_gate.zip"
  output_file_mode = "0644"

  source {
    content  = file("${local.lambda_src_path}/apply_gate/handler.py")
    filename = "handler.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/__init__.py")
    filename = "shared/__init__.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/github.py")
    filename = "shared/github.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/github_oidc.py")
    filename = "shared/github_oidc.py"
  }
}

resource "aws_iam_role" "apply_gate" {
  name                 = "${var.project}-apply-gate"
  permissions_boundary = var.org_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "apply_gate" {
  name = "${var.project}-apply-gate"
  role = aws_iam_role.apply_gate.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadSigningKey"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/platform/override-signing-key"
      },
      {
        Sid    = "PlanBucketAccess"
        Effect = "Allow"
        Action = [
          "s3:GetObject",
          "s3:PutObject",
        ]
        Resource = "arn:aws:s3:::${var.project}-ci-plan-artifacts-${var.aws_account_id}/*"
      },
      {
        Sid      = "AssumeTeamRoles"
        Effect   = "Allow"
        Action   = "sts:AssumeRole"
        Resource = "arn:aws:iam::${var.aws_account_id}:role/${var.project}-ci-team-*"
      },
      {
        Sid    = "ReadGitHubAppCredentials"
        Effect = "Allow"
        Action = "ssm:GetParameter"
        Resource = [
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/platform/github-app-id",
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/platform/github-app-key",
        ]
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "apply_gate_logs" {
  role       = aws_iam_role.apply_gate.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "apply_gate" {
  function_name    = "${var.project}-apply-gate"
  role             = aws_iam_role.apply_gate.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128
  filename         = data.archive_file.apply_gate.output_path
  source_code_hash = data.archive_file.apply_gate.output_base64sha256

  environment {
    variables = {
      SIGNING_KEY_PARAM   = "/${var.project}/platform/override-signing-key"
      PLAN_BUCKET         = "${var.project}-ci-plan-artifacts-${var.aws_account_id}"
      PROJECT             = var.project
      ACCOUNT_ID          = var.aws_account_id
      CREDENTIAL_DURATION = "900"
    }
  }
}

################################################################################
# CI Broker — validates repo→team via GitHub API, returns STS credentials
################################################################################

data "archive_file" "ci_broker" {
  type             = "zip"
  output_path      = "${path.module}/builds/ci_broker.zip"
  output_file_mode = "0644"

  source {
    content  = file("${local.lambda_src_path}/ci_broker/handler.py")
    filename = "handler.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/__init__.py")
    filename = "shared/__init__.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/github.py")
    filename = "shared/github.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/github_oidc.py")
    filename = "shared/github_oidc.py"
  }
}

resource "aws_iam_role" "ci_broker" {
  name                 = "${var.project}-ci-broker"
  permissions_boundary = var.org_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "ci_broker" {
  name = "${var.project}-ci-broker"
  role = aws_iam_role.ci_broker.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ReadGitHubAppCredentials"
        Effect = "Allow"
        Action = "ssm:GetParameter"
        Resource = [
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/platform/github-app-id",
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/platform/github-app-key",
        ]
      },
      {
        Sid    = "AssumeTeamAndDeployRoles"
        Effect = "Allow"
        Action = "sts:AssumeRole"
        Resource = [
          "arn:aws:iam::${var.aws_account_id}:role/${var.project}-ci-team-*",
          "arn:aws:iam::${var.aws_account_id}:role/${var.project}-ci-deploy-*",
        ]
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "ci_broker_logs" {
  role       = aws_iam_role.ci_broker.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "ci_broker" {
  function_name    = "${var.project}-ci-broker"
  role             = aws_iam_role.ci_broker.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128
  filename         = data.archive_file.ci_broker.output_path
  source_code_hash = data.archive_file.ci_broker.output_base64sha256

  environment {
    variables = {
      AWS_ACCOUNT_ID       = var.aws_account_id
      PROJECT              = var.project
      GITHUB_ORG           = "javaBin"
      GITHUB_APP_ID_PARAM  = "/${var.project}/platform/github-app-id"
      GITHUB_APP_KEY_PARAM = "/${var.project}/platform/github-app-key"
    }
  }
}

################################################################################
# App Auth Sync — creates Cognito app clients requested via app.yaml `auth:`
#
# App Terraform writes /<project>/apps/<team>/<app>/auth (cognito-app-client
# module). The developer boundary only lets a team write under its own path,
# so the parameter name proves ownership; this Lambda never trusts a
# caller-supplied team. See terraform/lambda-src/app_auth_sync/handler.py.
################################################################################

data "archive_file" "app_auth_sync" {
  type             = "zip"
  output_path      = "${path.module}/builds/app_auth_sync.zip"
  output_file_mode = "0644"
  source_dir       = "${local.lambda_src_path}/app_auth_sync"
}

resource "aws_iam_role" "app_auth_sync" {
  name = "${var.project}-app-auth-sync"
  # The org boundary denies creating any role without one
  permissions_boundary = var.org_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "app_auth_sync" {
  name = "${var.project}-app-auth-sync"
  role = aws_iam_role.app_auth_sync.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "ManageAppClients"
        Effect = "Allow"
        Action = [
          "cognito-idp:CreateUserPoolClient",
          "cognito-idp:UpdateUserPoolClient",
          "cognito-idp:DeleteUserPoolClient",
          "cognito-idp:DescribeUserPoolClient",
          "cognito-idp:ListUserPoolClients",
          "cognito-idp:ListIdentityProviders",
        ]
        Resource = [var.internal_user_pool_arn, var.external_user_pool_arn]
      },
      {
        # ListUserPools does not support resource-level permissions
        Sid      = "FindPools"
        Effect   = "Allow"
        Action   = "cognito-idp:ListUserPools"
        Resource = "*"
      },
      {
        Sid    = "ReadAuthRequests"
        Effect = "Allow"
        Action = ["ssm:GetParameter", "ssm:GetParametersByPath"]
        Resource = [
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/apps",
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/apps/*",
        ]
      },
      {
        Sid      = "WriteClientAllowedGroups"
        Effect   = "Allow"
        Action   = ["dynamodb:PutItem", "dynamodb:DeleteItem"]
        Resource = var.auth_memberships_table_arn
      },
      {
        Sid    = "WriteClientCredentials"
        Effect = "Allow"
        Action = [
          "ssm:PutParameter",
          "ssm:DeleteParameter",
          "ssm:DeleteParameters",
          "ssm:AddTagsToResource",
        ]
        Resource = [
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/apps/*/*/cognito-client-id",
          "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/apps/*/*/cognito-client-secret",
        ]
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "app_auth_sync_logs" {
  role       = aws_iam_role.app_auth_sync.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "app_auth_sync" {
  function_name    = "${var.project}-app-auth-sync"
  role             = aws_iam_role.app_auth_sync.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 128
  filename         = data.archive_file.app_auth_sync.output_path
  source_code_hash = data.archive_file.app_auth_sync.output_base64sha256

  # One sync at a time, so concurrent changes to the same app cannot race
  # into two clients with the same name.
  reserved_concurrent_executions = 1

  environment {
    variables = {
      PROJECT           = var.project
      MEMBERSHIPS_TABLE = var.auth_memberships_table_name
    }
  }
}

resource "aws_cloudwatch_event_rule" "app_auth_sync_trigger" {
  name        = "${var.project}-app-auth-sync-trigger"
  description = "app.yaml auth: request created, changed or deleted"

  event_pattern = jsonencode({
    source      = ["aws.ssm"]
    detail-type = ["Parameter Store Change"]
    detail = {
      name      = [{ wildcard = "/${var.project}/apps/*/*/auth" }]
      operation = ["Create", "Update", "Delete"]
    }
  })
}

resource "aws_cloudwatch_event_target" "app_auth_sync" {
  rule      = aws_cloudwatch_event_rule.app_auth_sync_trigger.name
  target_id = "invoke-app-auth-sync"
  arn       = aws_lambda_function.app_auth_sync.arn
}

resource "aws_lambda_permission" "app_auth_sync_eventbridge" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app_auth_sync.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.app_auth_sync_trigger.arn
}

# Daily safety net: re-sync every request and clean up orphaned clients, in
# case an EventBridge delivery was missed.
resource "aws_cloudwatch_event_rule" "app_auth_sync_reconcile" {
  name                = "${var.project}-app-auth-sync-reconcile"
  description         = "Daily reconcile of app Cognito clients"
  schedule_expression = "cron(30 3 * * ? *)"
}

resource "aws_cloudwatch_event_target" "app_auth_sync_reconcile" {
  rule      = aws_cloudwatch_event_rule.app_auth_sync_reconcile.name
  target_id = "reconcile-app-auth-sync"
  arn       = aws_lambda_function.app_auth_sync.arn
  input     = jsonencode({ action = "reconcile" })
}

resource "aws_lambda_permission" "app_auth_sync_reconcile" {
  statement_id  = "AllowEventBridgeReconcile"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app_auth_sync.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.app_auth_sync_reconcile.arn
}

################################################################################
# Plan Review — LLM risk review of app Terraform plans (javabin-plan-review)
#
# App tf-plan jobs run with team CI roles, which the developer boundary keeps
# away from Bedrock. tf-plan invokes this Lambda (via the broker OIDC role)
# with its GitHub OIDC token; the Lambda reviews the plan text in the plan
# bucket and writes risk.json, which team roles cannot write themselves.
################################################################################

locals {
  plan_bucket = "${var.project}-ci-plan-artifacts-${var.aws_account_id}"
}

data "archive_file" "plan_review" {
  type             = "zip"
  output_path      = "${path.module}/builds/plan_review.zip"
  output_file_mode = "0644"

  source {
    content  = file("${local.lambda_src_path}/plan_review/handler.py")
    filename = "handler.py"
  }
  # Same prompt and schema as platform CI's own review
  source {
    content  = file("${path.module}/../../../scripts/review-plan.py")
    filename = "review_plan.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/__init__.py")
    filename = "shared/__init__.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/github_oidc.py")
    filename = "shared/github_oidc.py"
  }
  source {
    content  = file("${local.lambda_src_path}/shared/slack.py")
    filename = "shared/slack.py"
  }
}

resource "aws_iam_role" "plan_review" {
  name = "${var.project}-plan-review"
  # The org boundary denies creating any role without one
  permissions_boundary = var.org_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "plan_review" {
  name = "${var.project}-plan-review"
  role = aws_iam_role.plan_review.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "ReadPlanText"
        Effect   = "Allow"
        Action   = "s3:GetObject"
        Resource = "arn:aws:s3:::${local.plan_bucket}/plans/*/plan-output.txt"
      },
      {
        Sid    = "WriteReview"
        Effect = "Allow"
        Action = "s3:PutObject"
        Resource = [
          "arn:aws:s3:::${local.plan_bucket}/plans/*/risk.json",
          "arn:aws:s3:::${local.plan_bucket}/plans/*/review.json",
        ]
      },
      {
        Sid      = "BedrockInferenceProfile"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:Converse"]
        Resource = "arn:aws:bedrock:${var.region}:${var.aws_account_id}:inference-profile/eu.anthropic.*"
      },
      {
        # Cross-region inference profiles route to EU foundation models;
        # the condition only allows them via our inference profiles.
        Sid      = "BedrockFoundationModels"
        Effect   = "Allow"
        Action   = ["bedrock:InvokeModel", "bedrock:Converse"]
        Resource = "arn:aws:bedrock:eu-*::foundation-model/anthropic.*"
        Condition = {
          StringLike = {
            "bedrock:InferenceProfileArn" = "arn:aws:bedrock:${var.region}:${var.aws_account_id}:inference-profile/eu.anthropic.*"
          }
        }
      },
      {
        Sid      = "SlackWebhook"
        Effect   = "Allow"
        Action   = "ssm:GetParameter"
        Resource = "arn:aws:ssm:${var.region}:${var.aws_account_id}:parameter/${var.project}/slack/platform-override-alerts-webhook"
      },
    ]
  })
}

resource "aws_iam_role_policy_attachment" "plan_review_logs" {
  role       = aws_iam_role.plan_review.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "plan_review" {
  function_name    = "${var.project}-plan-review"
  role             = aws_iam_role.plan_review.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 300
  memory_size      = 256
  filename         = data.archive_file.plan_review.output_path
  source_code_hash = data.archive_file.plan_review.output_base64sha256

  environment {
    variables = {
      PROJECT     = var.project
      PLAN_BUCKET = local.plan_bucket
    }
  }
}

################################################################################
# App Secret Rollout — restart an app when one of its secrets changes
#
# ECS reads task secrets only at task start. Teams can set their own secret
# values (javabin secret set) but have no ecs:UpdateService, so this Lambda
# forces a new deployment of {team}-{app} on every secret update.
################################################################################

data "archive_file" "app_secret_rollout" {
  type             = "zip"
  output_path      = "${path.module}/builds/app_secret_rollout.zip"
  output_file_mode = "0644"
  source_dir       = "${local.lambda_src_path}/app_secret_rollout"
}

resource "aws_iam_role" "app_secret_rollout" {
  name = "${var.project}-app-secret-rollout"
  # The org boundary denies creating any role without one
  permissions_boundary = var.org_boundary_arn

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "lambda.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })
}

resource "aws_iam_role_policy" "app_secret_rollout" {
  name = "${var.project}-app-secret-rollout"
  role = aws_iam_role.app_secret_rollout.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Sid      = "RestartAppServices"
      Effect   = "Allow"
      Action   = "ecs:UpdateService"
      Resource = "arn:aws:ecs:${var.region}:${var.aws_account_id}:service/${var.project}-platform/*"
    }]
  })
}

resource "aws_iam_role_policy_attachment" "app_secret_rollout_logs" {
  role       = aws_iam_role.app_secret_rollout.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWSLambdaBasicExecutionRole"
}

resource "aws_lambda_function" "app_secret_rollout" {
  function_name    = "${var.project}-app-secret-rollout"
  role             = aws_iam_role.app_secret_rollout.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128
  filename         = data.archive_file.app_secret_rollout.output_path
  source_code_hash = data.archive_file.app_secret_rollout.output_base64sha256

  environment {
    variables = {
      PROJECT = var.project
    }
  }
}

resource "aws_cloudwatch_event_rule" "app_secret_rollout_trigger" {
  name        = "${var.project}-app-secret-rollout-trigger"
  description = "An app secret value was changed"

  event_pattern = jsonencode({
    source      = ["aws.ssm"]
    detail-type = ["Parameter Store Change"]
    detail = {
      name      = [{ prefix = "/${var.project}/apps/" }]
      operation = ["Update"]
    }
  })
}

resource "aws_cloudwatch_event_target" "app_secret_rollout" {
  rule      = aws_cloudwatch_event_rule.app_secret_rollout_trigger.name
  target_id = "invoke-app-secret-rollout"
  arn       = aws_lambda_function.app_secret_rollout.arn
}

resource "aws_lambda_permission" "app_secret_rollout_eventbridge" {
  statement_id  = "AllowEventBridge"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.app_secret_rollout.function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.app_secret_rollout_trigger.arn
}
