# Task 2f — Lambda Functions

**Status:** Not started
**Phase:** 2 (Platform Infrastructure)
**Depends on:** 2e (monitoring — needs SNS topic ARNs)
**Blocks:** 2g (CI workflow uses plan review Lambda)

## Objective

Adapt all Lambda functions from the reference implementation and create new ones.

## Context

Read `phases/phase-2-platform.md` Task 6 for full spec.
Reference Lambda source: `terraform/lambda-src/` (copied from ai-platform-infra).

**Key adaptations:**
- Replace GitLab source enrichment → GitHub OIDC session tag parsing
- Replace `ai-platform-*` → `javabin-*` naming
- Replace `/ai-platform/*` → `/javabin/*` SSM paths
- Auto-tagger becomes compliance reporter (report, don't fix)
- Add Security Hub as finding source for slack-alert
- Extract shared pricing utility
- Add team-provisioner (NEW) and override-cleanup (NEW)

## Deliverables

### `terraform/platform/lambdas/`
- `main.tf` — All Lambda functions, IAM roles, schedules, SNS subscriptions
- `variables.tf` — project, region, account_id, topic ARNs
- `outputs.tf` — function ARNs

### Lambda source code in `terraform/lambda-src/`

#### 1. `slack_alert/handler.py` (adapt)
- Trigger: SNS (javabin-alerts, javabin-security)
- LLM risk analysis via Bedrock (Claude Haiku)
- Pricing API for cost hints
- Dedup (5-min window)
- Console URL building
- **GitHub attribution:** Parse OIDC sub claim (`repo:javaBin/{name}:ref:...`) and
  session tags (GitHubActor, GitHubSHA, GitHubRepo, GitHubRunID) for Slack links
- SSM: `/javabin/slack/infra-alerts-webhook`, `/javabin/slack/cost-alerts-webhook`

#### 2. `cost_report/handler.py` (adapt)
- Trigger: EventBridge (Monday 08:00 UTC)
- Weekly breakdown: WoW, MTD, projected
- Per-project tag breakdown (NEW — reference only did per-service)
- LLM narrative + spike root cause
- SSM: `/javabin/slack/cost-alerts-webhook`

#### 3. `daily_cost_check/handler.py` (adapt)
- Trigger: EventBridge (daily 08:00 UTC)
- Yesterday vs 7-day average, silent if no spikes
- No Bedrock exclusion (Javabin doesn't use Bedrock for AI workloads)

#### 4. `auto_tagger/handler.py` → `compliance_reporter/handler.py` (rewrite)
- Trigger: EventBridge (Create events)
- Change: Do NOT auto-tag. Post to Slack when untagged resources detected.
- Tags are injected at creation via CI — reporter catches leaks

#### 5. `team_provisioner/handler.py` (NEW)
- Trigger: GitHub repository dispatch from javaBin/registry
- Syncs teams across Google Workspace, GitHub, Cognito, IAM Identity Center
- Creates AWS Budgets per team
- SSM: `/javabin/platform/google-admin-sa`, `/javabin/platform/github-app-key`
- This is complex — will be implemented incrementally (Google blocked for now)

#### 6. `override_cleanup/handler.py` (NEW)
- Trigger: EventBridge (hourly)
- Deletes SSM parameters under `/javabin/overrides/*` older than 24 hours
- Prevents stale pre-approved overrides

### Shared pricing utility
Extract Pricing API logic into a Lambda layer or shared module under
`terraform/lambda-src/shared/pricing.py`. Used by slack-alert and plan review.

## Rules
- All secrets via SSM — webhook URLs, API keys read at runtime
- LLM calls use Bedrock (confirm model availability from discovery)
- Lambdas fail gracefully — alerts still send if LLM is unavailable
- team_provisioner is a stub until Google Admin access is available
- Python 3.12 runtime for all Python Lambdas
