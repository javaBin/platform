################################################################################
# Account security guardrails
#
# Account-level settings that close Security Hub findings at no or low cost.
# None of them changes how running workloads behave: they block new public
# sharing, encrypt new volumes, and add detection.
################################################################################

# EC2.182: no EBS snapshot can be shared publicly. No public snapshots exist.
resource "aws_ebs_snapshot_block_public_access" "main" {
  state = "block-all-sharing"
}

# Codifies the live value (already block-new-sharing) so drift is caught.
resource "aws_ec2_image_block_public_access" "main" {
  state = "block-new-sharing"
}

# EC2.7: new EBS volumes are encrypted with the AWS-managed aws/ebs key.
# Existing volumes are untouched; only legacy EB auto-scaling groups launch
# volumes in this account, and their service-linked role can use aws/ebs.
resource "aws_ebs_encryption_by_default" "main" {
  enabled = true
}

# SSM.7: block public sharing of SSM documents. The account shares none.
resource "aws_ssm_service_setting" "document_public_sharing" {
  setting_id    = "arn:aws:ssm:${var.region}:${var.aws_account_id}:servicesetting/ssm/documents/console/public-sharing-permission"
  setting_value = "Disable"
}

# IAM Access Analyzer, external access only (free). Flags resource policies
# that grant access outside the account. Unused-access analysis is paid per
# role/user and is left off.
resource "aws_accessanalyzer_analyzer" "external_access" {
  analyzer_name = "${var.project}-external-access"
  type          = "ACCOUNT"
}

# Inspector: ECR image and Lambda package scanning (about $5-10/month at
# current volume). EC2 scanning is off (only legacy EB instances, not
# SSM-managed) and Lambda code scanning is off (cost); the matching Security
# Hub controls are disabled with reasons in the detection-tuning change.
resource "aws_inspector2_enabler" "main" {
  account_ids    = [var.aws_account_id]
  resource_types = ["ECR", "LAMBDA"]
}

################################################################################
# Security tampering → javabin-security SNS
#
# Alerts when anyone turns off logging, detection or the guardrails above.
# This is the cheap replacement for the CIS CloudWatch metric-filter alarms
# (CloudTrail → CloudWatch Logs costs ingestion). Rule 1 (iam_changes) and
# the console-login rule already cover IAM and sign-in.
################################################################################

resource "aws_cloudwatch_event_rule" "security_tampering" {
  name        = "${var.project}-security-tampering"
  description = "Detect changes that disable logging, detection or account guardrails"

  event_pattern = jsonencode({
    detail-type = ["AWS API Call via CloudTrail"]
    detail = {
      eventSource = [
        "cloudtrail.amazonaws.com",
        "config.amazonaws.com",
        "kms.amazonaws.com",
        "guardduty.amazonaws.com",
        "securityhub.amazonaws.com",
        "access-analyzer.amazonaws.com",
        "ec2.amazonaws.com",
        "ssm.amazonaws.com",
        "inspector2.amazonaws.com",
      ]
      eventName = [
        "StopLogging",
        "DeleteTrail",
        "UpdateTrail",
        "PutEventSelectors",
        "StopConfigurationRecorder",
        "DeleteConfigurationRecorder",
        "DeleteDeliveryChannel",
        "DisableKey",
        "ScheduleKeyDeletion",
        "DeleteDetector",
        "UpdateDetector",
        "DisassociateFromMasterAccount",
        "DisableSecurityHub",
        "BatchDisableStandards",
        "DeleteAnalyzer",
        "DisableSnapshotBlockPublicAccess",
        "DisableEbsEncryptionByDefault",
        "DisableImageBlockPublicAccess",
        "UpdateServiceSetting",
        "Disable",
        # Changes that hide findings or narrow recording rather than turn a
        # service off. Platform applies that touch these also alert, which
        # is the intended audit trail.
        "CreateFilter",
        "UpdateFilter",
        "CreateAutomationRules",
        "BatchUpdateAutomationRules",
        "UpdateStandardsControl",
        "BatchUpdateStandardsControlAssociations",
        "PutConfigurationRecorder",
        "EnableSnapshotBlockPublicAccess",
      ]
    }
  })
}

resource "aws_cloudwatch_event_target" "security_tampering_sns" {
  rule      = aws_cloudwatch_event_rule.security_tampering.name
  target_id = "send-to-security-sns"
  arn       = aws_sns_topic.security.arn
}
