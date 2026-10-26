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

**Flow:** Re-read the request → validate (pool, client_type, https/localhost URLs without `#fragment`, scopes, allowed_groups) → upsert client `{team}-{app}` in the requested pool with that pool's fixed sign-in IdP list (a client_type change replaces the client, since the secret setting is immutable) → remove it from the other pool → write `/javabin/apps/{team}/{app}/cognito-client-id` (String), for confidential clients `cognito-client-secret` (SecureString), and `cognito-client-identity-provider` (String), tagged `team={team}`, **only when the value changed** (secret first, ID last) → record `allowed_groups` in `javabin-auth-memberships` → write `auth-status`. A missing request deletes the client, its parameters and `auth-status`. Reconcile also removes clients whose credential parameter exists but whose request is gone; clients created by other means are never touched. The Lambda checks URL scheme and fragments only; the host rules (one of the app's own hosts or `extra_hosts`) run in `generate-modules.py` at generate time, and a team writing the request parameter directly bypasses them.

**Slots:** a request one level down, `/javabin/apps/{team}/{app}/login-N/auth` (written by service-routing for each `routing.login` rule), gets its own client `{team}-{app}.login-N`; its credentials, `cognito-client-identity-provider` and `auth-status` go under `/javabin/apps/{team}/{app}/login-N/`. `.` cannot occur in app names, so slot clients never collide with another app's client. Other subpaths are ignored.

**Identity providers:** every client in a pool gets exactly the pool's list, from `INTERNAL_IDENTITY_PROVIDERS` / `EXTERNAL_IDENTITY_PROVIDERS` (comma-separated; set from the identity module's `internal_identity_providers` = `["GoogleSAML"]` and `external_identity_providers` = `["COGNITO"]`). With a single IdP the hosted UI redirects straight to it, so internal apps show no password form or sign-up link. Reconcile overwrites console edits to the list (reported as drift). If a configured IdP does not exist in the pool, the request is skipped with an `ERROR` log and status `error` (reconcile lists it under `error`): nothing is created, moved or changed, and the existing client keeps working. Internal-pool requests asking for `aws.cognito.signin.user.admin` are rejected as invalid (that scope would let a user rewrite the email auth-claims trusts).

**Errors:** reconcile syncs every app even if one fails, and returns normally (raising would make the async EventBridge/CI invoke retry the whole run twice). The CloudWatch alarm `javabin-app-auth-sync-skipped` (metric filter on `client left unchanged` or `Sync failed for`) posts to `javabin-alerts` for a skipped request or a failed app. A failing change event still raises (Lambda `Errors`).

**auth-status** (`/javabin/apps/{team}/{app}/auth-status`, String JSON, team-readable): `status` (`synced` / `invalid` / `error`), `error`, `client_id`, `client_id_changed`, `client_id_changed_at` (when the ID last changed, carried over between syncs), `client_type`, `pool`, `pool_domain`, `identity_providers`, `identity_provider`, `callback_urls`, `logout_urls`, `scopes`, `allowed_groups`, `unknown_groups` (no `group#` item in the memberships table), `empty_groups`, `changed_fields`, `drift_corrected` (fields a *reconcile* changed back: someone edited the client outside the platform; also logged as `WARN drift`), `request_version`, `synced_at`. When the JSON would exceed 4096 bytes, list fields are dropped (URLs first) and `truncated: true` is set; it is always valid JSON. `scripts/auth-doctor.py` reads it.

**Who can write it:** the team SSO permission sets (`javabin-team-{team}`, and `javabin-developer` in `terraform/org`) deny writes to `auth-status`, `cognito-client-*`, `auth` and `db-master-password`. Team CI roles and gate-vended apply credentials (developer boundary, which allows `ssm:PutParameter` under `/javabin/apps/{team}/*`) can still write them, as they always could for `cognito-client-*`. The effect stays inside the team's own app: a forged auth-status, or a changed credential that the next sync puts back (and one extra restart). Treat auth-status as a report, not an authority.

**Environment vars:** `PROJECT`, `MEMBERSHIPS_TABLE`, `INTERNAL_IDENTITY_PROVIDERS`, `EXTERNAL_IDENTITY_PROVIDERS`, `INTERNAL_POOL_DOMAIN`, `EXTERNAL_POOL_DOMAIN`. Platform CI queues a reconcile after every apply (`scripts/reconcile-app-auth.sh`).

**Concurrency:** reserved concurrency 1, so two changes to one app cannot race into duplicate clients.

**Manual run:** `aws lambda invoke --function-name javabin-app-auth-sync --payload '{"action":"reconcile"}' --cli-binary-format raw-in-base64-out /dev/stdout`

## auth-claims

**Trigger:** Cognito pre-token-generation (V2) on the internal pool (`javabin-internal`), every sign-in and token refresh
**Purpose:** Refuses tokens for accounts outside the Workspace domain, puts the user's registry groups into `cognito:groups` (ID and access token), and refuses sign-in when the app client has `allowed_groups` and the user is in none of them.

**Domain check:** with `REQUIRED_EMAIL_DOMAIN` set (internal pool: `var.google_workspace_domain`, `java.no`), a token is issued only when the email ends with `@java.no` (case-insensitive) **and** the user signed in through one of `FEDERATED_IDENTITY_PROVIDERS` (the internal pool's IdP list, `GoogleSAML`) or has `email_verified=true`. Federated users are recognised by the `identities` attribute, or by their `<Provider>_` username. Anyone else gets "Sign in with your @java.no account." on the app's callback. Every IdP in that list is trusted to vouch for the email (no `email_verified` check), so it must release only Workspace accounts; see the comment on `internal_identity_providers` before adding one. Exception: IdPs in `EMAIL_VERIFIED_IDENTITY_PROVIDERS` (default `Google`, the OIDC IdP) also need `email_verified=true`, which the pool maps from Google's claim; the identity module refuses `Google` in the list until its OAuth client is in SSM and `google_workspace_domain` is set. A function without `REQUIRED_EMAIL_DOMAIN` skips the check. The internal pool also has self sign-up disabled (`allow_admin_create_user_only`).

**Environment vars:** `MEMBERSHIPS_TABLE`, `REQUIRED_EMAIL_DOMAIN`, `FEDERATED_IDENTITY_PROVIDERS`, `EMAIL_VERIFIED_IDENTITY_PROVIDERS` (optional, default `Google`)

**Data:** `javabin-auth-memberships` (DynamoDB). `group#<name>` items (members' emails) are written by team-provisioner on registry merges; `client#<client_id>` items (`allowed_groups`) by app-auth-sync. Memberships are cached for 60 s per container.

**External pool:** the same code runs as `javabin-auth-claims-external` with `REGISTRY_GROUPS=false`. Anyone can sign up in the external pool with any email, so registry groups (keyed by email) are never added there; native Cognito groups still go into `cognito:groups`, and per-client `allowed_groups` are still enforced.

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
**Purpose:** ECS resolves task secrets only at start, so after a team sets a secret value (`javabin secret set`) this forces a new deployment of `{team}-{app}` in `javabin-platform`. Teams have no `ecs:UpdateService` themselves (it would also allow changing networking and task definitions). Cognito client parameters (`cognito-client-id`, `-secret`, `-identity-provider`) also restart the app: app-auth-sync writes them only when the value changes (replaced client, IdP cutover), so running tasks never keep a deleted client ID and a no-op reconcile restarts nothing. Ignored: `auth`, `auth-status` (rewritten every sync), `db-master-password`. A missing service is logged and skipped.

## Shared Module: pricing

`terraform/lambda-src/shared/pricing.py` — provides `estimate_cost()` for enriching alerts with cost impact. Uses AWS Pricing API (us-east-1 endpoint). Caches pricing data across Lambda invocations.
