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

ECS `SERVICE_TASK_START_IMPAIRED` events from the platform cluster (a crash-looping service) are posted to #javabin-infra-alerts at most once per service per UTC day.

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
| `/javabin/platform/atlassian-org-id` | Atlassian organisation ID |
| `/javabin/platform/atlassian-api-key` | Atlassian org admin API key (unscoped — full org admin) |

### Atlassian group sync (`sync_atlassian`)

Invoked by the registry's `provision-groups.yml` via `scripts/provision-atlassian.py`, which resolves
`groups/atlassian.yaml` to members' java.no emails. `atlassian.py` makes each declared Atlassian group
contain exactly those accounts (creates missing groups, adds members, removes everyone else, including
personal-email accounts). A group with `all_java_no_accounts: true` also gets every active managed
`@java.no` account — everyone with a java.no Google account. Undeclared groups are never touched; `org-admins`, `site-admins` and
IdP-synced groups are refused.

**Report-only by default.** Writes happen only when the `lambdas` module's `atlassian_sync_apply` is
`true` (env `ATLASSIAN_SYNC_APPLY`); the event can't turn them on. Each run posts per-group counts
(no email addresses) to #javabin-infra-alerts and returns the full plan in the Lambda response.

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

**Flow:** Re-read the request → validate (pool, client_type, https/localhost URLs, scopes, allowed_groups) → upsert client `{team}-{app}` in the requested pool with every pool IdP + `COGNITO` (a client_type change replaces the client, since the secret setting is immutable) → remove it from the other pool → write `/javabin/apps/{team}/{app}/cognito-client-id` (String) and, for confidential clients, `cognito-client-secret` (SecureString), tagged `team={team}` → record `allowed_groups` in `javabin-auth-memberships`. A missing request deletes the client and both parameters. Reconcile also removes clients whose credential parameter exists but whose request is gone; clients created by other means are never touched.

**Concurrency:** reserved concurrency 1, so two changes to one app cannot race into duplicate clients.

**Manual run:** `aws lambda invoke --function-name javabin-app-auth-sync --payload '{"action":"reconcile"}' --cli-binary-format raw-in-base64-out /dev/stdout`

## auth-claims

**Trigger:** Cognito pre-token-generation (V2) on the internal pool (`javabin-internal`), every sign-in and token refresh
**Purpose:** Puts the user's registry groups into `cognito:groups` (ID and access token), and refuses sign-in when the app client has `allowed_groups` and the user is in none of them.

**Data:** `javabin-auth-memberships` (DynamoDB). `group#<name>` items (members' emails) are written by team-provisioner on registry merges; `client#<client_id>` items (`allowed_groups`) by app-auth-sync. Memberships are cached for 60 s per container.

**Lives in:** `terraform/platform/identity/` (the pool references it, so it can't sit in `lambdas/` without a module cycle).

## plan-review

**Trigger:** Invoked by app `tf-plan` (via the `javabin-ci-app-broker` OIDC role) after the plan is uploaded
**Purpose:** LLM risk review of app Terraform plans with platform credentials. Team CI roles have no Bedrock access (developer boundary), and any code in a plan job runs with them.

**Flow:** Verify the job's GitHub OIDC token (aud `javabin-plan-review`) → plan must be under `plans/javaBin/<repo>/` → read `plan-output.txt` → review with Bedrock using `scripts/review-plan.py` (bundled as `review_plan.py`), up to 3 attempts with backoff → write `risk.json` (read by apply-gate) and `review.json` → on HIGH/FAILED for pushes to main, post to the override Slack channel → return risk + markdown for the PR comment.

Team roles are denied writing `risk.json`, `review.json` and `override.json` in the plan bucket. `risk.json` is written as FAILED before the review starts, so a crash or timeout still leaves a verdict the gate (and a board override) can act on. The plan text is uploaded by the plan job itself, so the review catches mistakes; it is not a control against code in the job forging its own plan text.

| SSM Parameter | Use |
|---------------|-----|
| `/javabin/slack/platform-override-alerts-webhook` | HIGH/FAILED alerts |

## app-secret-rollout

**Trigger:** EventBridge "Parameter Store Change" (Update) under `/javabin/apps/`
**Purpose:** ECS resolves task secrets only at start, so after a team sets a secret value (`javabin secret set`) this forces a new deployment of `{team}-{app}` in `javabin-platform`. Teams have no `ecs:UpdateService` themselves (it would also allow changing networking and task definitions). Platform-managed parameters (`auth`, `cognito-client-*`, `db-master-password`) are ignored; a missing service is logged and skipped.

## Shared Module: pricing

`terraform/lambda-src/shared/pricing.py` — provides `estimate_cost()` for enriching alerts with cost impact. Uses AWS Pricing API (us-east-1 endpoint). Caches pricing data across Lambda invocations.
