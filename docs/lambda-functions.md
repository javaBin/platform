# Lambda Functions

Source code in `terraform/lambda-src/{function}/handler.py`. Shared module in `terraform/lambda-src/shared/`.
Terraform in `terraform/platform/lambdas/`.
Log groups `/aws/lambda/javabin-{function}` are declared in `lambdas/log-groups.tf` (365-day retention); add every new function to `local.platform_lambda_names` (enforced by `scripts/tests/test_lambda_log_groups.py`).

## slack-alert

**Trigger:** SNS subscriptions (both `javabin-alerts` and `javabin-security` topics)
**Purpose:** Receives CloudWatch/EventBridge/GuardDuty/Security Hub alerts, uses Bedrock LLM to assess risk, posts formatted Block Kit messages to Slack.

| SSM Parameter | Channel |
|---------------|---------|
| `/javabin/slack/platform-resource-alerts-webhook` | #javabin-infra-alerts |
| `/javabin/slack/platform-cost-alerts-webhook` | #javabin-cost-alerts |

**Environment vars:** `INFRA_WEBHOOK_PARAM`, `COST_WEBHOOK_PARAM`, `SECURITY_TOPIC_ARN`, `PROJECT_PREFIX`, `GITHUB_ORG_URL`, `DEPLOY_REGION`

Features: GitHub OIDC attribution (extracts actor/repo/SHA from session tags), cost estimation via shared pricing module, Security Hub finding formatting.

ECS `SERVICE_TASK_START_IMPAIRED` events from the `javabin-platform` cluster only (a crash-looping service) are posted to #javabin-infra-alerts at most once per service per UTC day. Legacy clusters (vaultwarden, cakeredux, moresleep-test, dennis-test) are not covered. As with the other deduplicated alerts, the dedup key is written before the Slack post, so a failed post suppresses that service's alert for the rest of the day; if the dedup table is unreadable, dedup fails open.

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

**Trigger:** Registry repo merge events (direct invocation from registry CI via `javabin-ci-registry`); `sync_team_access` from platform CI after apply; `sync_access_members` from EventBridge every 3 hours (`javabin-team-provisioner-access-schedule`)
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

### Team Google group (`sync_google_group`)

Each team gets the group `team-{name}@java.no`. Members are kept in step with the registry: a member
with `role: maintainer` is a Google **MANAGER**, everyone else a **MEMBER**. A differing role is
changed in place. Members not in the registry are removed, except **OWNER**s: an owner is only ever
set by a Workspace admin by hand (e.g. `platform@java.no`), so owners are never demoted or removed and
are reported as `kept_owners`. Team members must have a `@java.no` account: the group has
`allowExternalMembers=false`, so a non-java.no address (legacy `email:` field) is not added and is
reported as `skipped_external` with a Slack `:warning:`, while the rest of the sync continues.

After the member sync, `enforce_team_group_settings` makes the group's mail readable by its members.
It reads the Groups Settings API and writes only the keys that differ:

| Setting | Value |
|---------|-------|
| `whoCanViewGroup` | `ALL_MEMBERS_CAN_VIEW` (members read the conversations at groups.google.com/a/java.no/g/team-{name}) |
| `isArchived` / `archiveOnly` | `true` / `false` (keep an archive, still deliver mail) |
| `whoCanViewMembership` | `ALL_IN_DOMAIN_CAN_VIEW` |
| `whoCanJoin` | `INVITED_CAN_JOIN` (the registry is the only way in) |
| `allowWebPosting` | `true` |
| `allowExternalMembers` | `false` |

Not enforced, so outside senders reach the group as before: `whoCanPostMessage`, message and spam
moderation, collaborative inbox, and posting as the group. The current `whoCanPostMessage` and
`messageModerationLevel` are returned in the result for visibility.

The settings use their own token with only the `apps.groups.settings` scope. If that scope is not
delegated, the settings step is skipped with a Slack `:warning:` and member sync still succeeds. If
the Groups Settings API is disabled in GCP project `javabin-platform`, the step reports that.
Org groups from the registry's `groups.yaml` are not touched by this.

Members read the archive at groups.google.com only if the **Groups for Business** service is ON for
their org unit (Admin console > Apps > Google Workspace > Groups for Business); without it the archive
settings have no visible effect.

Delegated scopes the platform code requests (client ID 103162295681564397615). The console may grant
more; read the current list there first and add to it rather than pasting this one:
`admin.directory.group`, `admin.directory.group.member`, `admin.directory.user`,
`admin.directory.user.alias`, `gmail.send`, `gmail.settings.sharing`, `apps.groups.settings`
(all under `https://www.googleapis.com/auth/`).

### Team GitHub team (`sync_github_team`)

Each team gets the GitHub team `{name}`. Members without a `role:` field are team **maintainers**
(`GITHUB_DEFAULT_TEAM_ROLE`), so whoever registered the team can add its repos to it; `role: member`
makes someone a plain member. Registering a team (`javabin register-team`) writes no `role:`. A
member's existing role is changed in place, and logins not in the registry are removed. The Google
group keeps **MEMBER** as its default (only `role: maintainer` makes a Google MANAGER).

### Team AWS access (`sync_identity_center_group`, `sync_team_access`)

The team sync puts members in the Identity Center group `team-{name}` and gives that group the
permission set `javabin-team-{name}` on the account, with the customer-managed policy
`javabin-team-{name}-guardrails`. That policy is made by platform Terraform, which only learns of a
new team when platform CI runs on the registry's `teams-changed` dispatch, after the team sync.
Until it exists the team sync stops before the inline policy and the account assignment and reports
`guardrails: pending` (`:hourglass_flowing_sand:` in Slack): without the guardrails the inline
policy's `Action: *` would be unbounded.

Platform CI finishes the job: after every apply it queues `{"action": "sync_team_access"}`
(`scripts/sync-team-access.sh`, async). For every `team-*` Identity Center group whose guardrails
policy exists and is tagged `managed-by=terraform`, `repo=javaBin/platform`, it attaches the
guardrails, writes the inline policy, makes the account assignment and re-provisions the permission
set; teams without one (e.g. `platform`) are left alone. `{"teams": [...]}` limits the run. It posts
to #javabin-infra-alerts only when a team got access for the first time or something failed
(`CreateAccountAssignment` accepts an assignment that already exists without a conflict, so the
handler checks `ListAccountAssignments` first; if that lookup fails it stays quiet). Since it
runs on every apply it also rolls inline policy changes out to existing teams.

### How a member gets AWS access (`sync_aws_access_group`, `sync_access_members`)

Users reach Identity Center only through Google's SCIM auto-provisioning, and Google provisions only
the **direct** members of the groups the AWS app's *Group membership* setting lists (`drift@`,
`styret@` and `javabin-developer@java.no`; `SCIM_GROUPS` in the handler mirrors it). Google does not
expand nested groups, so `team-{name}@` or `kodesmia@` nested in `javabin-developer@` would give no
one a user. The flow is:

1. **Registry sync adds the person to the AWS access group.** The team sync makes every member's
   java.no address (from their `google` field) a direct MEMBER of `AWS_ACCESS_GROUP` (env var, set
   from Terraform `var.aws_access_group`, default `javabin-developer@java.no`). For kodesmia, the
   registry's `groups/access.yaml` declares the group with `flatten: true`:

   ```yaml
   - name: javabin-developer
     google: javabin-developer@java.no
     description: Google SCIM provisions direct members to IAM Identity Center
     groups: [kodesmia]
     flatten: true
   ```

   An access group with `flatten: true` gets the role groups' individual members (from the resolved
   members in the `sync_groups_and_heros` payload) instead of the nested group email. Both paths are
   additive, like the other access groups: nobody is removed. The result lists `added_members`,
   members present but not in kodesmia or any team as `preserved` (they are kept), and personal
   (non-java.no) addresses as `skipped_external` (they can't be provisioned).
2. **Google SCIM creates the Identity Center user**, some time later. The same registry run has
   already tried the Identity Center groups, so it skips the person and returns them in
   `not_in_identity_center` (only members actually in the group count in `member_count`).
3. **The scheduled run adds them to their groups.** Every 3 hours EventBridge invokes
   `{"action": "sync_access_members"}`. It reads the registry members the provisioner recorded in the
   memberships table (`team-{name}` and every cognito-enabled group, e.g. `kodesmia`), makes the
   recorded team members direct members of `AWS_ACCESS_GROUP` (covers teams synced before this
   existed or a failed add), and adds recorded members that now have an Identity Center user to the
   Identity Center group of the same name. It only adds; removals stay with the registry syncs. It
   posts to #javabin-infra-alerts only when someone was added or an add failed.

The Slack summary lists everyone without an Identity Center user with a reason
(`_identity_center_reasons`): **pending SCIM** (just added to `javabin-developer@`, or already a
direct member of a provisioned group; if that persists, check the Google app's provisioning
status), **can't be provisioned** (personal address, suspended in Google, not in Google Workspace)
or **not a direct member of `javabin-developer@`**. The response body carries the same map as
`not_in_identity_center`.

The provisioner does not create these users itself through the Identity Store API, although the
API would accept it: a user made that way has no link to the Google account, so suspending or
deleting the person in Google would not remove their AWS access (SCIM never manages it), and when
Google later tries to provision the same `UserName` its create conflicts with the existing user and
fails. Identity Center users stay SCIM-owned (`CreatedBy: SCIM/...`).

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

**Managed-login branding:** after the credentials, every client gets a managed-login style with Cognito's defaults (`CreateManagedLoginBranding`, `UseCognitoProvidedValues=True`), only if `DescribeManagedLoginBrandingByClient` finds none, so a style customised in the console is never changed. A pool domain on managed login (`ManagedLoginVersion` 2) has no sign-in page for a client without a style; on the classic hosted UI the style is unused. Deleting a client (request gone, pool switch, `client_type` change, orphan) deletes its style first. Reconcile backfills styles for existing clients. auth-status `branding`: `created` / `exists`. A failure here comes after the credentials are written but still makes the sync `error` (alarm).

**Identity providers:** every client in a pool gets exactly the pool's list, from `INTERNAL_IDENTITY_PROVIDERS` / `EXTERNAL_IDENTITY_PROVIDERS` (comma-separated; set from the identity module's `internal_identity_providers` = `["Google"]` and `external_identity_providers` = `["COGNITO"]`). With a single IdP the hosted UI redirects straight to it, so internal apps show no password form or sign-up link. Reconcile overwrites console edits to the list (reported as drift). If a configured IdP does not exist in the pool, the request is skipped with an `ERROR` log and status `error` (reconcile lists it under `error`): nothing is created, moved or changed, and the existing client keeps working. Platform-owned requests (`/javabin/apps/platform/...`; `platform` is never a registered team) may set `identity_providers` to override the list — the IdP probe client (`platform-idp-probe`, see cognito-google-setup.md) uses it; each IdP must exist in the pool and the internal pool never allows `COGNITO`; the field in any other team's request is invalid. Internal-pool requests asking for `aws.cognito.signin.user.admin` are rejected as invalid (that scope would let a user rewrite the email auth-claims trusts).

**Errors:** reconcile syncs every app even if one fails, and returns normally (raising would make the async EventBridge/CI invoke retry the whole run twice). The CloudWatch alarm `javabin-app-auth-sync-skipped` (metric filter on `client left unchanged` or `Sync failed for`) posts to `javabin-alerts` for a skipped request or a failed app. A failing change event still raises (Lambda `Errors`).

**auth-status** (`/javabin/apps/{team}/{app}/auth-status`, String JSON, team-readable): `status` (`synced` / `invalid` / `error`), `error`, `client_id`, `client_id_changed`, `client_id_changed_at` (when the ID last changed, carried over between syncs), `client_type`, `pool`, `pool_domain`, `identity_providers`, `identity_provider`, `callback_urls`, `logout_urls`, `scopes`, `allowed_groups`, `unknown_groups` (no `group#` item in the memberships table), `empty_groups`, `changed_fields`, `branding`, `drift_corrected` (fields a *reconcile* changed back: someone edited the client outside the platform; also logged as `WARN drift`), `request_version`, `synced_at`. When the JSON would exceed 4096 bytes, list fields are dropped (URLs first) and `truncated: true` is set; it is always valid JSON. `scripts/auth-doctor.py` reads it.

**Who can write it:** the team SSO permission sets (`javabin-team-{team}`, and `javabin-developer` in `terraform/org`) deny writes to `auth-status`, `cognito-client-*`, `auth` and `db-master-password`. Team CI roles and gate-vended apply credentials (developer boundary, which allows `ssm:PutParameter` under `/javabin/apps/{team}/*`) can still write them, as they always could for `cognito-client-*`. The effect stays inside the team's own app: a forged auth-status, or a changed credential that the next sync puts back (and one extra restart). Treat auth-status as a report, not an authority.

**Environment vars:** `PROJECT`, `MEMBERSHIPS_TABLE`, `INTERNAL_IDENTITY_PROVIDERS`, `EXTERNAL_IDENTITY_PROVIDERS`, `INTERNAL_POOL_DOMAIN`, `EXTERNAL_POOL_DOMAIN`. Platform CI queues a reconcile after every apply (`scripts/reconcile-app-auth.sh`).

**Concurrency:** reserved concurrency 1, so two changes to one app cannot race into duplicate clients.

**Manual run:** `aws lambda invoke --function-name javabin-app-auth-sync --payload '{"action":"reconcile"}' --cli-binary-format raw-in-base64-out /dev/stdout`

## app-peers-sync

**Trigger:** EventBridge "Parameter Store Change" for `/javabin/apps/*/*/network` (Create/Update/Delete), plus a daily `{"action": "reconcile"}` at 03:45 UTC
**Purpose:** App-to-app calls declared in `app.yaml` (`calls:` / `provides:`). The request's path identifies the app (only its team can write there); the security group in the request must be that app's own (`{team}-{app}-tasks`, tag `team={team}`), so a team cannot make the Lambda open another team's group.

**Flow:** Every run recomputes everything from all requests (event payloads are ignored) → an edge exists when the caller `calls` the callee and the callee lists the caller in `consumers` → ingress rule on the callee's SG from the caller's SG on the callee port, tagged `managed-by=javabin-app-peers-sync` (rules the team added itself are never touched) → Cognito resource server `{t}-{a}` per callee with scopes and client-credentials client `m2m.{t}-{a}` per caller requesting scopes (internal pool, 60 min tokens, granted = requested ∩ allowed; no grants = OAuth disabled) → writes `peer-{t}-{a}-url`, `peer-{t}-{a}-scopes` (the scopes the caller's client really holds for that callee, or `none`), `m2m-client-id`, `m2m-client-secret` (SecureString) and `peers-status` into the caller's path, only when a value changed. Parameters are tagged `managed-by=javabin-app-peers-sync` on create, and only parameters with that tag are overwritten or deleted (a revoke that fails for any reason other than not-found is reported). Credential parameters stay for every caller that requests scopes, even when its Cognito sync failed this run; the secret is written before the ID so a failed rotation is retried. Parameters, clients and servers nobody needs are deleted. An app whose request is invalid is frozen, not removed: its parameters, managed rules, resource server and client scopes stay, as long as every valid side of an edge still declares it (the valid side can still withdraw); it gets `{"error": ...}` in `peers-status`, and its peers see `callee-invalid` / `caller-invalid`.

**Audits (reconcile only, Slack `#javabin-infra-alerts`, never fixed automatically):** Cloud Map services whose name does not match their `team` tag or that have instances registered by another ECS service; records in the namespace's Route53 private zone that are not A records Cloud Map wrote for a registered instance (teams are also denied changing the zone in the developer boundary); network interfaces in the VPC carrying `{t}-{a}-tasks` that are not a task of ECS service `{t}-{a}` in `javabin-platform` (tasks in every cluster are checked, and ENIs of non-ECS resources are reported too).

**Concurrency:** reserved concurrency 1.

**Manual run:** `aws lambda invoke --function-name javabin-app-peers-sync --payload '{"action":"reconcile"}' --cli-binary-format raw-in-base64-out /dev/stdout`

| SSM Parameter | Use |
|---------------|-----|
| `/javabin/slack/platform-resource-alerts-webhook` | Audit findings and errors |

## auth-claims

**Trigger:** Cognito pre-token-generation (V2) on the internal pool (`javabin-internal`), every sign-in and token refresh
**Purpose:** Refuses tokens for accounts outside the Workspace domain, puts the user's registry groups into `cognito:groups` (ID and access token), and refuses sign-in when the app client has `allowed_groups` and the user is in none of them.

**Domain check:** with `REQUIRED_EMAIL_DOMAIN` set (internal pool: `var.google_workspace_domain`, `java.no`), a token is issued only when the email ends with `@java.no` (case-insensitive) **and** the user signed in through one of `FEDERATED_IDENTITY_PROVIDERS` (the internal pool's IdP list, `Google`) or has `email_verified=true`. Federated users are recognised by the `identities` attribute, or by their `<Provider>_` username. Anyone else gets "Sign in with your @java.no account." on the app's callback. Every IdP in that list is trusted to vouch for the email (no `email_verified` check), so it must release only Workspace accounts; see the comment on `internal_identity_providers` before adding one. Exception: IdPs in `EMAIL_VERIFIED_IDENTITY_PROVIDERS` (default `Google`, the OIDC IdP) also need `email_verified=true`, which the pool maps from Google's claim; the identity module refuses `Google` in the list until its OAuth client is in SSM and `google_workspace_domain` is set. A function without `REQUIRED_EMAIL_DOMAIN` skips the check. The internal pool also has self sign-up disabled (`allow_admin_create_user_only`).

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
**Purpose:** ECS resolves task secrets only at start, so after a team sets a secret value (`javabin secret set`) this forces a new deployment of `{team}-{app}` in `javabin-platform`. Teams have no `ecs:UpdateService` themselves (it would also allow changing networking and task definitions). Cognito client parameters (`cognito-client-id`, `-secret`, `-identity-provider`) also restart the app: app-auth-sync writes them only when the value changes (replaced client, IdP cutover), so running tasks never keep a deleted client ID and a no-op reconcile restarts nothing. Ignored: `auth`, `auth-status` (rewritten every sync), `db-master-password`, `network`, `peers-status`. Peer URLs and scopes and `m2m-client-*` (app-peers-sync) do restart the caller. A missing service is logged and skipped.

## Shared Module: pricing

`terraform/lambda-src/shared/pricing.py` — provides `estimate_cost()` for enriching alerts with cost impact. Uses AWS Pricing API (us-east-1 endpoint). Caches pricing data across Lambda invocations.
