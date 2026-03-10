################################################################################
# Lambda Functions — slack-alert, cost-report, daily-cost-check,
#                    compliance-reporter, override-cleanup, team-provisioner
################################################################################

locals {
  lambda_src_path = "${path.module}/../../lambda-src"
  github_org_url  = "https://github.com/javaBin"
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
}

data "archive_file" "compliance_reporter" {
  type             = "zip"
  output_path      = "${path.module}/builds/compliance_reporter.zip"
  output_file_mode = "0644"
  source_dir       = "${local.lambda_src_path}/compliance_reporter"
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
        Sid    = "Bedrock"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:Converse",
        ]
        Resource = "arn:aws:bedrock:${var.region}:${var.aws_account_id}:inference-profile/eu.anthropic.*"
      },
      {
        Sid    = "PricingRead"
        Effect = "Allow"
        Action = "pricing:GetProducts"
        # Pricing API is global and only supports * for resource
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
        Sid    = "Bedrock"
        Effect = "Allow"
        Action = [
          "bedrock:InvokeModel",
          "bedrock:Converse",
        ]
        Resource = "arn:aws:bedrock:${var.region}:${var.aws_account_id}:inference-profile/eu.anthropic.*"
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
      INFRA_WEBHOOK_PARAM = "/javabin/slack/platform-resource-alerts-webhook"
      COST_WEBHOOK_PARAM  = "/javabin/slack/platform-cost-alerts-webhook"
      SECURITY_TOPIC_ARN  = var.security_topic_arn
      PROJECT_PREFIX      = var.project
      GITHUB_ORG_URL      = local.github_org_url
      DEPLOY_REGION       = var.region
    }
  }
}

resource "aws_lambda_function" "cost_report" {
  function_name    = "${var.project}-cost-report"
  role             = aws_iam_role.cost_report.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.cost_report.output_path
  source_code_hash = data.archive_file.cost_report.output_base64sha256

  environment {
    variables = {
      COST_WEBHOOK_PARAM = "/javabin/slack/platform-cost-alerts-webhook"
      DEPLOY_REGION      = var.region
    }
  }
}

resource "aws_lambda_function" "daily_cost_check" {
  function_name    = "${var.project}-daily-cost-check"
  role             = aws_iam_role.daily_cost_check.arn
  handler          = "handler.handler"
  runtime          = "python3.12"
  timeout          = 30
  memory_size      = 128
  filename         = data.archive_file.daily_cost_check.output_path
  source_code_hash = data.archive_file.daily_cost_check.output_base64sha256

  environment {
    variables = {
      COST_WEBHOOK_PARAM = "/javabin/slack/platform-cost-alerts-webhook"
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
  timeout          = 60
  memory_size      = 256
  filename         = data.archive_file.team_provisioner.output_path
  source_code_hash = data.archive_file.team_provisioner.output_base64sha256

  environment {
    variables = {
      INFRA_WEBHOOK_PARAM      = "/javabin/slack/platform-resource-alerts-webhook"
      GOOGLE_SA_PARAM          = "/javabin/platform/google-admin-sa"
      GOOGLE_ADMIN_EMAIL_PARAM = "/javabin/platform/google-admin-email"
      GITHUB_APP_ID_PARAM      = "/javabin/platform/github-app-id"
      GITHUB_APP_KEY_PARAM     = "/javabin/platform/github-app-key"
      ACCOUNT_ID               = var.aws_account_id
      GITHUB_ORG               = "javaBin"
      ALERTS_TOPIC_ARN         = var.alerts_topic_arn
      COGNITO_INTERNAL_POOL_ID = var.internal_user_pool_id
      IDENTITY_STORE_ID        = var.identity_store_id
      SSO_INSTANCE_ARN         = var.sso_instance_arn
      PROJECT                  = var.project
    }
  }
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
        "CreateBucket", "RunInstances", "CreateDBInstance",
        "CreateService", "CreateFunction20150331", "CreateLoadBalancer",
        "CreateSecurityGroup", "CreateNatGateway", "CreateVpc", "CreateSubnet",
        "CreateTargetGroup",
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

################################################################################
# Apply Gate — credential broker for Terraform apply
################################################################################

data "archive_file" "apply_gate" {
  type             = "zip"
  output_path      = "${path.module}/builds/apply_gate.zip"
  output_file_mode = "0644"
  source_dir       = "${local.lambda_src_path}/apply_gate"
}

resource "aws_iam_role" "apply_gate" {
  name                 = "${var.project}-apply-gate"
  permissions_boundary = "arn:aws:iam::${var.aws_account_id}:policy/${var.project}-developer-boundary"

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
        Sid      = "AssumeAppRoles"
        Effect   = "Allow"
        Action   = "sts:AssumeRole"
        Resource = "arn:aws:iam::${var.aws_account_id}:role/${var.project}-ci-app-*"
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
