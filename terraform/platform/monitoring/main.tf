################################################################################
# SNS Topics for Alerts
################################################################################

resource "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts"

  tags = {
    Name = "${var.project}-alerts"
    team = "shared"
  }
}

resource "aws_sns_topic" "security" {
  name = "${var.project}-security"

  tags = {
    Name = "${var.project}-security"
    team = "shared"
  }
}

################################################################################
# SNS Topic Policies — allow AWS services to publish
################################################################################

resource "aws_sns_topic_policy" "alerts" {
  arn = aws_sns_topic.alerts.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowCostAnomalyPublish"
        Effect    = "Allow"
        Principal = { Service = "costalerts.amazonaws.com" }
        Action    = "SNS:Publish"
        Resource  = aws_sns_topic.alerts.arn
      },
      {
        Sid       = "AllowBudgetsPublish"
        Effect    = "Allow"
        Principal = { Service = "budgets.amazonaws.com" }
        Action    = "SNS:Publish"
        Resource  = aws_sns_topic.alerts.arn
      },
      {
        Sid       = "AllowEventsPublish"
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "SNS:Publish"
        Resource  = aws_sns_topic.alerts.arn
      }
    ]
  })
}

resource "aws_sns_topic_policy" "security" {
  arn = aws_sns_topic.security.arn

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AllowEventsPublish"
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "SNS:Publish"
        Resource  = aws_sns_topic.security.arn
      }
    ]
  })
}

################################################################################
# EventBridge Rules — Security Events → javabin-security SNS
################################################################################

# ------------------------------------------------------------------
# Rule 1: IAM changes (roles, policies)
# ------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "iam_changes" {
  name        = "${var.project}-iam-changes"
  description = "Detect IAM role/policy changes"

  event_pattern = jsonencode({
    source      = ["aws.iam"]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventSource = ["iam.amazonaws.com"]
      eventName = [
        { "prefix" : "Create" },
        { "prefix" : "Delete" },
        { "prefix" : "Put" },
        { "prefix" : "Attach" },
        { "prefix" : "Detach" },
      ]
    }
  })
}

resource "aws_cloudwatch_event_target" "iam_changes_sns" {
  rule      = aws_cloudwatch_event_rule.iam_changes.name
  target_id = "send-to-security-sns"
  arn       = aws_sns_topic.security.arn
}

# ------------------------------------------------------------------
# Rule 2: Console logins
# ------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "console_login" {
  name        = "${var.project}-console-login"
  description = "Detect AWS console logins"

  event_pattern = jsonencode({
    source      = ["aws.signin"]
    detail-type = ["AWS Console Sign In via CloudTrail"]
  })
}

resource "aws_cloudwatch_event_target" "console_login_sns" {
  rule      = aws_cloudwatch_event_rule.console_login.name
  target_id = "send-to-security-sns"
  arn       = aws_sns_topic.security.arn
}

# ------------------------------------------------------------------
# Rule 3: Config compliance changes (catches untagged resources)
# ------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "config_compliance" {
  name        = "${var.project}-config-compliance-change"
  description = "Detect when resources become non-compliant with tag rules"

  event_pattern = jsonencode({
    source      = ["aws.config"]
    detail-type = ["Config Rules Compliance Change"]
    detail = {
      configRuleName = ["${var.project}-required-tags"]
      newEvaluationResult = {
        complianceType = ["NON_COMPLIANT"]
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "config_compliance_sns" {
  rule      = aws_cloudwatch_event_rule.config_compliance.name
  target_id = "send-to-security-sns"
  arn       = aws_sns_topic.security.arn
}

# ------------------------------------------------------------------
# Rule 4: Significant resource creation
# ------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "resource_creation" {
  name        = "${var.project}-resource-creation"
  description = "Detect creation of significant AWS resources"

  # Intentionally scoped to major compute/storage services — NOT a wildcard.
  # A {"prefix": "aws."} match would fire ~10,000 events/hour (CreateLogStream,
  # CreateNetworkInterface, etc.) overwhelming SNS and making alerts unusable.
  # The resource-tagger Lambda uses a wildcard because it has generic fallback;
  # these security alerts need a curated list for signal-to-noise.
  event_pattern = jsonencode({
    source = [
      "aws.s3", "aws.ec2", "aws.rds", "aws.ecs",
      "aws.elasticloadbalancing", "aws.lambda",
      "aws.sqs", "aws.dynamodb",
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

resource "aws_cloudwatch_event_target" "resource_creation_sns" {
  rule      = aws_cloudwatch_event_rule.resource_creation.name
  target_id = "send-to-security-sns"
  arn       = aws_sns_topic.security.arn
}

# ------------------------------------------------------------------
# Rule 5: Resource modifications and deletions
# ------------------------------------------------------------------

resource "aws_cloudwatch_event_rule" "resource_modification" {
  name        = "${var.project}-resource-modification"
  description = "Detect modifications and deletions of significant AWS resources"

  # Same rationale as resource_creation — curated list for alert signal quality.
  event_pattern = jsonencode({
    source = [
      "aws.s3", "aws.ec2", "aws.rds", "aws.ecs",
      "aws.elasticloadbalancing", "aws.lambda",
    ]
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventName = [
        { "prefix" : "Modify" },
        { "prefix" : "Update" },
        { "prefix" : "Delete" },
        { "prefix" : "Stop" },
        { "prefix" : "Terminate" },
      ]
    }
  })
}

resource "aws_cloudwatch_event_target" "resource_modification_sns" {
  rule      = aws_cloudwatch_event_rule.resource_modification.name
  target_id = "send-to-security-sns"
  arn       = aws_sns_topic.security.arn
}

################################################################################
# AWS Config Recorder — needed for Config Rules to evaluate resources
################################################################################

resource "aws_config_configuration_recorder" "main" {
  name     = "${var.project}-recorder"
  role_arn = aws_iam_role.config_role.arn

  recording_group {
    all_supported                 = true
    include_global_resource_types = false
  }
}

resource "aws_config_delivery_channel" "main" {
  name           = "${var.project}-config-channel"
  s3_bucket_name = aws_s3_bucket.config_logs.id
  depends_on     = [aws_config_configuration_recorder.main]
}

resource "aws_config_configuration_recorder_status" "main" {
  name       = aws_config_configuration_recorder.main.name
  is_enabled = true
  depends_on = [aws_config_delivery_channel.main]
}

################################################################################
# S3 Bucket for Config Logs
################################################################################

resource "aws_s3_bucket" "config_logs" {
  bucket = "${var.project}-config-${var.aws_account_id}"

  tags = {
    Name = "${var.project}-config"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "config_logs" {
  bucket = aws_s3_bucket.config_logs.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "aws:kms"
    }
  }
}

resource "aws_s3_bucket_public_access_block" "config_logs" {
  bucket = aws_s3_bucket.config_logs.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_policy" "config_logs" {
  bucket = aws_s3_bucket.config_logs.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid       = "AWSConfigBucketPermissionsCheck"
        Effect    = "Allow"
        Principal = { Service = "config.amazonaws.com" }
        Action    = "s3:GetBucketAcl"
        Resource  = aws_s3_bucket.config_logs.arn
      },
      {
        Sid       = "AWSConfigBucketDelivery"
        Effect    = "Allow"
        Principal = { Service = "config.amazonaws.com" }
        Action    = "s3:PutObject"
        Resource  = "${aws_s3_bucket.config_logs.arn}/*"
        Condition = {
          StringEquals = { "s3:x-amz-acl" = "bucket-owner-full-control" }
        }
      }
    ]
  })
}

################################################################################
# IAM Role for AWS Config
################################################################################

resource "aws_iam_role" "config_role" {
  name = "${var.project}-config-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "config.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = {
    Name = "${var.project}-config-role"
  }
}

resource "aws_iam_role_policy_attachment" "config_role" {
  role       = aws_iam_role.config_role.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AWS_ConfigRole"
}

################################################################################
# Config Rule — Required Tags (service, team, managed-by)
################################################################################

resource "aws_config_config_rule" "required_tags" {
  name = "${var.project}-required-tags"

  source {
    owner             = "AWS"
    source_identifier = "REQUIRED_TAGS"
  }

  input_parameters = jsonencode({
    tag1Key = "service"
    tag2Key = "team"
    tag3Key = "managed-by"
  })

  depends_on = [aws_config_configuration_recorder.main]
}

################################################################################
# Cost Anomaly Detection → SNS
################################################################################

resource "aws_ce_anomaly_monitor" "main" {
  name              = "${var.project}-anomaly-monitor"
  monitor_type      = "DIMENSIONAL"
  monitor_dimension = "SERVICE"
}

resource "aws_ce_anomaly_subscription" "alerts" {
  name = "${var.project}-anomaly-alerts"

  monitor_arn_list = [aws_ce_anomaly_monitor.main.arn]

  frequency = "IMMEDIATE"

  threshold_expression {
    dimension {
      key           = "ANOMALY_TOTAL_IMPACT_ABSOLUTE"
      match_options = ["GREATER_THAN_OR_EQUAL"]
      values        = ["10"]
    }
  }

  subscriber {
    type    = "SNS"
    address = aws_sns_topic.alerts.arn
  }
}

################################################################################
# GuardDuty — threat detection with ECS Runtime Monitoring
################################################################################

resource "aws_guardduty_detector" "main" {
  enable = true

  tags = {
    Name = "${var.project}-guardduty"
  }
}

resource "aws_guardduty_detector_feature" "runtime_monitoring" {
  detector_id = aws_guardduty_detector.main.id
  name        = "RUNTIME_MONITORING"
  status      = "ENABLED"

  # Order must match AWS API response: EKS, ECS, EC2
  additional_configuration {
    name   = "EKS_ADDON_MANAGEMENT"
    status = "DISABLED"
  }

  additional_configuration {
    name   = "ECS_FARGATE_AGENT_MANAGEMENT"
    status = "ENABLED"
  }

  additional_configuration {
    name   = "EC2_AGENT_MANAGEMENT"
    status = "DISABLED"
  }
}

# GuardDuty findings route through EventBridge → security SNS
resource "aws_cloudwatch_event_rule" "guardduty_findings" {
  name        = "${var.project}-guardduty-findings"
  description = "Route GuardDuty findings to SNS"

  event_pattern = jsonencode({
    source      = ["aws.guardduty"]
    detail-type = ["GuardDuty Finding"]
  })
}

resource "aws_cloudwatch_event_target" "guardduty_findings_sns" {
  rule      = aws_cloudwatch_event_rule.guardduty_findings.name
  target_id = "send-to-security-sns"
  arn       = aws_sns_topic.security.arn
}

################################################################################
# Security Hub — normalized findings from GuardDuty, Config, and more
#
# When both Security Hub and GuardDuty are enabled in the same account,
# GuardDuty findings are automatically imported into Security Hub.
# Config findings are also imported when Config is enabled.
################################################################################

resource "aws_securityhub_account" "main" {}

# Enable AWS Foundational Security Best Practices standard
resource "aws_securityhub_standards_subscription" "aws_foundational" {
  standards_arn = "arn:aws:securityhub:${var.region}::standards/aws-foundational-security-best-practices/v/1.0.0"
  depends_on    = [aws_securityhub_account.main]
}

# Route Security Hub findings with severity >= HIGH to security SNS.
# MEDIUM findings are too noisy (SSL, logging, tags) — these are covered
# by Config rules and compliance reporter already.
resource "aws_cloudwatch_event_rule" "securityhub_findings" {
  name        = "${var.project}-securityhub-findings"
  description = "Route Security Hub findings (HIGH and above) to SNS"

  event_pattern = jsonencode({
    source      = ["aws.securityhub"]
    detail-type = ["Security Hub Findings - Imported"]
    detail = {
      findings = {
        Severity = {
          Label = ["HIGH", "CRITICAL"]
        }
      }
    }
  })
}

resource "aws_cloudwatch_event_target" "securityhub_findings_sns" {
  rule      = aws_cloudwatch_event_rule.securityhub_findings.name
  target_id = "send-to-security-sns"
  arn       = aws_sns_topic.security.arn
}

################################################################################
# DynamoDB — alert dedup table (Security Hub + compliance)
################################################################################

resource "aws_dynamodb_table" "alert_dedup" {
  name         = "${var.project}-alert-dedup"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "finding_key"

  attribute {
    name = "finding_key"
    type = "S"
  }

  ttl {
    attribute_name = "expires_at"
    enabled        = true
  }

  tags = {
    Name = "${var.project}-alert-dedup"
  }
}

################################################################################
# Cost Allocation Tags
################################################################################

# Cost allocation tags must exist on at least one billed resource before
# activation. Tags are added in phases:
#   Phase 1: team, service, environment, managed-by
#   Phase 2: repo (in default_tags, now on resources)
#   Phase 3 (after resource-tagger runs): created-by, commit
resource "aws_ce_cost_allocation_tag" "tags" {
  for_each = toset(["team", "service", "environment", "managed-by", "repo"])
  tag_key  = each.key
  status   = "Active"
}
