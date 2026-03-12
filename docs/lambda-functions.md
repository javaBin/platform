# Lambda Functions

Source code in `terraform/lambda-src/{function}/handler.py`. Shared module in `terraform/lambda-src/shared/`.
Terraform in `terraform/platform/lambdas/`.

## slack-alert

**Trigger:** SNS subscriptions (both `javabin-alerts` and `javabin-security` topics)
**Purpose:** Receives CloudWatch/EventBridge/GuardDuty/Security Hub alerts, uses Bedrock LLM to assess risk, posts formatted Block Kit messages to Slack.

| SSM Parameter | Channel |
|---------------|---------|
| `/javabin/slack/platform-resource-alerts-webhook` | #javabin-infra-alerts |
| `/javabin/slack/platform-cost-alerts-webhook` | #javabin-cost-alerts |

**Environment vars:** `INFRA_WEBHOOK_PARAM`, `COST_WEBHOOK_PARAM`, `SECURITY_TOPIC_ARN`, `PROJECT_PREFIX`, `GITHUB_ORG_URL`, `DEPLOY_REGION`

Features: GitHub OIDC attribution (extracts actor/repo/SHA from session tags), cost estimation via shared pricing module, Security Hub finding formatting.

Uses the `javabin-alert-dedup` DynamoDB table for deduplication of alerts (prevents repeated notifications for the same finding).

## cost-report

**Trigger:** EventBridge schedule — Monday 08:00 UTC
**Purpose:** Weekly cost breakdown by service and project tag, with LLM narrative summary.

| SSM Parameter | Channel |
|---------------|---------|
| `/javabin/slack/platform-cost-alerts-webhook` | #javabin-cost-alerts |

Uses Cost Explorer API for 7-day and 30-day cost data. Includes per-project tag breakdown.

## daily-cost-check

**Trigger:** EventBridge schedule — daily 08:00 UTC
**Purpose:** Silent spike detection. Only posts to Slack when today's cost exceeds 2x the 7-day average. No message on normal days.

| SSM Parameter | Channel |
|---------------|---------|
| `/javabin/slack/platform-cost-alerts-webhook` | #javabin-cost-alerts |

## compliance-reporter

**Trigger:** EventBridge rules (resource creation events: EC2, Lambda, ECS, RDS, S3, DynamoDB, SQS)
**Purpose:** Reports untagged resources to Slack. Does NOT auto-tag — CI handles tagging via `default_tags`.

| SSM Parameter | Channel |
|---------------|---------|
| `/javabin/slack/platform-resource-alerts-webhook` | #javabin-infra-alerts |

Filters by identity: only reports resources created by known CI/platform identities (configured via `compliance_reporter_identities` variable).

## override-cleanup

**Trigger:** EventBridge schedule — hourly
**Purpose:** Deletes stale SSM parameters under `/javabin/platform-overrides/` older than 24 hours. Override tokens are meant to be single-use; this cleans up any that weren't consumed.

No Slack integration. Logs cleanup actions to CloudWatch.

## securityhub-summary

**Trigger:** EventBridge schedule — Monday 08:00 UTC
**Purpose:** Weekly summary of active HIGH/CRITICAL Security Hub findings. Reuses the slack-alert code with the `summary_handler` entry point.

| SSM Parameter | Channel |
|---------------|---------|
| `/javabin/slack/platform-resource-alerts-webhook` | #javabin-infra-alerts |

Queries Security Hub for active findings at HIGH and CRITICAL severity, aggregates by resource type and finding title, and posts a formatted summary to Slack.

## team-provisioner

**Trigger:** (Future) Registry repo merge events
**Purpose:** Syncs team definitions across Google Workspace, GitHub, Cognito, and IAM.

**Status:** Stub only — logs event and returns success. Blocked on Google Admin access.

## Shared Module: pricing

`terraform/lambda-src/shared/pricing.py` — provides `estimate_cost()` for enriching alerts with cost impact. Uses AWS Pricing API (us-east-1 endpoint). Caches pricing data across Lambda invocations.
