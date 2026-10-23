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

**Trigger:** Registry repo merge events (via `provision-app.yml` workflow dispatch)
**Purpose:** Syncs team definitions from registry YAML across Google Groups, GitHub teams, AWS Budgets (80% warning + 200% enforcement thresholds), Cognito groups, and Identity Center groups. Also handles hero account provisioning.

| SSM Parameter | Purpose |
|---------------|---------|
| `/javabin/platform/google-admin-sa` | GCP service account JSON key (domain-wide delegation) |
| `/javabin/platform/google-admin-email` | Admin email for Google Admin SDK impersonation |
| `/javabin/platform/github-app-id` | GitHub App ID for team management |
| `/javabin/platform/github-app-key` | GitHub App private key |
| `/javabin/platform/github-app-client-secret` | GitHub App client secret |

## budget-enforcer

**Trigger:** SNS notification from AWS Budgets (200% threshold)
**Purpose:** Scales a team's ECS services to `desired_count=0` when spending exceeds 200% of their monthly budget. Does NOT destroy resources — services can be scaled back up after resolution.

**Flow:** Parse budget name (`javabin-team-{team}`) → list ECS services tagged with team → scale to zero → post Slack alert.

| SSM Parameter | Channel |
|---------------|---------|
| `/javabin/slack/platform-cost-alerts-webhook` | #javabin-cost-alerts |

**Environment vars:** `ECS_CLUSTER` (default: `javabin-platform`)

## resource-tagger

**Trigger:** EventBridge rule matching all AWS service creation events (`{"prefix": "aws."}` source, `Create*`/`Run*` event names)
**Purpose:** Auto-tags newly created AWS resources with `created-by` (actor) and `commit` (SHA) parsed from the CloudTrail session name. Tags are set via AWS Resource Groups Tagging API, outside Terraform management — no drift or plan noise.

**Session name format:** `{actor}-{sha8}-{run_id}` (enriched in CI workflows)

Idempotent: skips resources that already have a `created-by` tag (preserves original creator).

**Environment vars:** `AWS_ACCOUNT_ID`

## app-auth-sync

**Trigger:** EventBridge "Parameter Store Change" for `/javabin/apps/*/*/auth` (Create/Update/Delete), plus a daily `{"action": "reconcile"}` at 03:30 UTC
**Purpose:** Creates, updates and deletes the Cognito app clients requested by `app.yaml` `auth:`. The request parameter's path identifies the team (only that team can write there), so no caller-supplied identity is trusted.

**Flow:** Re-read the request → validate (pool, https URLs, scopes) → upsert client `{team}-{app}` in the requested pool with every pool IdP + `COGNITO` → remove it from the other pool → write `/javabin/apps/{team}/{app}/cognito-client-id` (String) and `cognito-client-secret` (SecureString), tagged `team={team}`. A missing request deletes the client and both parameters. Reconcile also removes clients whose credential parameter exists but whose request is gone; clients created by other means are never touched.

**Concurrency:** reserved concurrency 1, so two changes to one app cannot race into duplicate clients.

**Manual run:** `aws lambda invoke --function-name javabin-app-auth-sync --payload '{"action":"reconcile"}' --cli-binary-format raw-in-base64-out /dev/stdout`

## Shared Module: pricing

`terraform/lambda-src/shared/pricing.py` — provides `estimate_cost()` for enriching alerts with cost impact. Uses AWS Pricing API (us-east-1 endpoint). Caches pricing data across Lambda invocations.
