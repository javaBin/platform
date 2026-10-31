################################################################################
# Detection tuning — GuardDuty filters and Security Hub control/finding
# suppression for patterns verified as benign or as accepted risk.
#
# Every entry states why. docs/platform-modules.md ("Detection tuning") lists
# them all; review the list when the reason stops being true.
################################################################################

# ------------------------------------------------------------------
# GuardDuty archive filters (apply to new findings only)
# ------------------------------------------------------------------

# The team-provisioner Lambda syncs Cognito group descriptions on a schedule
# (cognito-idp:UpdateGroup); GuardDuty flags it as Impact:IAMUser/
# AnomalousBehavior. Only that type, that principal and that service.
resource "aws_guardduty_filter" "team_provisioner_cognito_sync" {
  name        = "team-provisioner-cognito-sync"
  description = "Benign: team-provisioner Lambda syncs Cognito groups"
  detector_id = aws_guardduty_detector.main.id
  action      = "ARCHIVE"
  rank        = 1

  finding_criteria {
    criterion {
      field  = "type"
      equals = ["Impact:IAMUser/AnomalousBehavior"]
    }
    criterion {
      field  = "resource.accessKeyDetails.userName"
      equals = ["${var.project}-team-provisioner"]
    }
    criterion {
      field  = "service.action.awsApiCallAction.serviceName"
      equals = ["cognito-idp.amazonaws.com"]
    }
  }
}

# Deliberately no filter for team SSO roles' CredentialAccess:IAMUser/
# AnomalousBehavior on ssm:GetParameter. The team permission set is
# Action "*" scoped by StringEqualsIfExists aws:ResourceTag/team, so it does
# not limit reads to the team's own path, and this finding type is exactly
# what a stolen SSO session reading secrets produces. Triage it by hand.

# ------------------------------------------------------------------
# Security Hub controls disabled per standard
# ------------------------------------------------------------------

locals {
  cis_1_2_arn  = "arn:aws:securityhub:::ruleset/cis-aws-foundations-benchmark/v/1.2.0"
  fsbp_1_0_arn = "arn:aws:securityhub:${var.region}::standards/aws-foundational-security-best-practices/v/1.0.0"

  reason_no_cw_logs = "CloudTrail is not sent to CloudWatch Logs (ingestion cost). Equivalent alerts come from EventBridge rules (iam-changes, console-login, resource-creation/modification, security-tampering) to javabin-security, and GuardDuty covers unauthorized API calls and root use."

  disabled_controls = merge(
    # CIS 1.2.0 metric-filter alarms all need CloudTrail -> CloudWatch Logs.
    { for n in range(1, 15) : "cis/CloudWatch.${n}" => {
      standard = local.cis_1_2_arn
      control  = "CloudWatch.${n}"
      reason   = local.reason_no_cw_logs
    } },
    {
      "cis/CloudTrail.5" = {
        standard = local.cis_1_2_arn
        control  = "CloudTrail.5"
        reason   = local.reason_no_cw_logs
      }
      "fsbp/CloudTrail.5" = {
        standard = local.fsbp_1_0_arn
        control  = "CloudTrail.5"
        reason   = local.reason_no_cw_logs
      }
      "fsbp/GuardDuty.7" = {
        standard = local.fsbp_1_0_arn
        control  = "GuardDuty.7"
        reason   = "No EKS clusters in the account."
      }
      "fsbp/Macie.1" = {
        standard = local.fsbp_1_0_arn
        control  = "Macie.1"
        reason   = "Macie not adopted: cost not justified for current S3 data. Revisit if PII is stored in S3."
      }
      "fsbp/EC2.172" = {
        standard = local.fsbp_1_0_arn
        control  = "EC2.172"
        reason   = "VPC block-public-access cannot be on: the public ALB and NAT gateway need the internet gateway."
      }
      "fsbp/Inspector.1" = {
        standard = local.fsbp_1_0_arn
        control  = "Inspector.1"
        reason   = "Inspector EC2 scanning off: only legacy Elastic Beanstalk instances (not SSM-managed), being migrated to ECS."
      }
      "fsbp/Inspector.3" = {
        standard = local.fsbp_1_0_arn
        control  = "Inspector.3"
        reason   = "Inspector Lambda code scanning off (cost). Lambda package scanning (Inspector.4) is on."
      }
    },
    # Interface endpoints cost ~$15/month each across two AZs; AWS API traffic
    # leaves through NAT over TLS, which is acceptable here.
    { for c in ["EC2.10", "EC2.55", "EC2.56", "EC2.57", "EC2.58", "EC2.60"] : "fsbp/${c}" => {
      standard = local.fsbp_1_0_arn
      control  = c
      reason   = "No VPC interface endpoints (~$15/month each). AWS API traffic uses the NAT gateway over TLS."
    } },
  )
}

resource "aws_securityhub_standards_control_association" "disabled" {
  for_each = local.disabled_controls

  standards_arn       = each.value.standard
  security_control_id = each.value.control
  association_status  = "DISABLED"
  updated_reason      = each.value.reason

  depends_on = [aws_securityhub_account.main]
}

# ------------------------------------------------------------------
# Security Hub automation rules — suppress specific accepted findings
# ------------------------------------------------------------------

# moresleep's database is intentionally publicly reachable (public API with
# existing external clients; no architecture change during migration).
# Narrowing its security group is tracked separately, so EC2.18/EC2.19 on that
# group stay open on purpose.
resource "aws_securityhub_automation_rule" "moresleep_public_rds" {
  rule_name   = "${var.project}-accepted-moresleep-public-rds"
  description = "dbmoresleep is intentionally publicly accessible (accepted risk)."
  rule_order  = 1
  rule_status = "ENABLED"

  criteria {
    compliance_security_control_id {
      comparison = "EQUALS"
      value      = "RDS.2"
    }
    compliance_security_control_id {
      comparison = "EQUALS"
      value      = "RDS.46"
    }
    resource_id {
      comparison = "EQUALS"
      value      = "arn:aws:rds:${var.region}:${var.aws_account_id}:db:dbmoresleep"
    }
  }

  actions {
    type = "FINDING_FIELDS_UPDATE"
    finding_fields_update {
      workflow {
        status = "SUPPRESSED"
      }
      note {
        text       = "Accepted risk: dbmoresleep is a public endpoint by design. Security group narrowing is tracked in the security audit (H3)."
        updated_by = "${var.project}-platform-terraform"
      }
    }
  }

  depends_on = [aws_securityhub_account.main]
}

# The platform ALB's port-80 listener only redirects to HTTPS.
resource "aws_securityhub_automation_rule" "alb_http_redirect" {
  rule_name   = "${var.project}-accepted-alb-http-redirect"
  description = "The platform ALB port-80 listener only redirects to HTTPS."
  rule_order  = 2
  rule_status = "ENABLED"

  criteria {
    compliance_security_control_id {
      comparison = "EQUALS"
      value      = "ELB.18"
    }
    resource_id {
      comparison = "EQUALS"
      value      = var.alb_http_listener_arn
    }
  }

  actions {
    type = "FINDING_FIELDS_UPDATE"
    finding_fields_update {
      workflow {
        status = "SUPPRESSED"
      }
      note {
        text       = "False positive: redirect-only listener (HTTP 301 to HTTPS)."
        updated_by = "${var.project}-platform-terraform"
      }
    }
  }

  depends_on = [aws_securityhub_account.main]
}

# App task definitions (terraform/modules/ecs-service) carry a platform init
# container that runs as root for one command, `chmod 1777 /tmp`, because
# Fargate creates the shared /tmp volume root-owned and the app runs as a
# non-root user. ECS.20 fails any task definition with a root container, so
# it would flag every app. The app container itself cannot be root: the
# generator and the module both reject compute.user 0/root. Scoped to
# team-prefixed families; the legacy CDK task definitions keep their findings.
# Security Hub allows 20 values per criterion, hence one rule per 20 teams.
resource "aws_securityhub_automation_rule" "ecs_tmp_init_root" {
  count = length(local.ecs_tmp_init_team_chunks)

  rule_name   = "${var.project}-accepted-ecs-tmp-init-root-${count.index}"
  description = "Platform /tmp init container in app task definitions runs as root (accepted)."
  rule_order  = 3
  rule_status = "ENABLED"

  criteria {
    compliance_security_control_id {
      comparison = "EQUALS"
      value      = "ECS.20"
    }

    dynamic "resource_id" {
      for_each = local.ecs_tmp_init_team_chunks[count.index]
      content {
        comparison = "PREFIX"
        value      = "arn:aws:ecs:${var.region}:${var.aws_account_id}:task-definition/${resource_id.value}-"
      }
    }
  }

  actions {
    type = "FINDING_FIELDS_UPDATE"
    finding_fields_update {
      workflow {
        status = "SUPPRESSED"
      }
      note {
        text       = "Accepted: the platform_tmp_init container runs as root only to chmod 1777 the /tmp volume; the app container runs as a non-root user (enforced by the generator)."
        updated_by = "${var.project}-platform-terraform"
      }
    }
  }

  depends_on = [aws_securityhub_account.main]
}

locals {
  ecs_tmp_init_team_chunks = chunklist(sort(var.registered_teams), 20)
}
