# CLAUDE.md — Javabin Platform Infrastructure

Terraform-managed AWS infrastructure for **Javabin** (java.no), the Norwegian Java User Group.

## Context

Javabin is a non-profit volunteer org (~4,000 members, ~70 active heroes). It runs JavaZone
(~3,600 attendees) and monthly meetups across Norway. This repo (`javaBin/platform`) manages
all shared AWS infrastructure. App repos manage their own resources via reusable modules
sourced from this repo.

**AWS Account:** 553637109631
**Region:** eu-central-1 (Frankfurt) — confirmed, all existing infra here
**Domains:** javazone.no, java.no, javabin.no, javabin.com, teknologihuset.no are Route53 zones. Apps can be hosted one label under javazone.no, java.no or javabin.no (wildcard certs on the ALB). aws.javabin.no / aws.java.no / aws.javazone.no redirect to the SSO portal (CloudFront, `dns/`).
**GitHub Org:** github.com/javaBin
**CI/CD:** GitHub Actions
**Bedrock:** All models available (no per-model access request needed)

### Existing Infrastructure (do not touch)
- **Coolify EC2** (eu-north-1): t4g.large at 13.53.169.151 — serves talks/2023-2025.javazone.no
- **Default-ALB** (eu-central-1): Routes to ECS services (moresleep, cakeredux, vaultwarden, talks)
- **ECS via CDK** (eu-central-1): 4 clusters (vaultwarden, moresleep-test, cakeredux, dennis-test)
- **Elastic Beanstalk** (eu-central-1): cakeredux (Green), submitthethird (Green), moresleep (Suspended)
- **GitHub OIDC provider**: Already exists for 8 repos (CFN stack in UPDATE_ROLLBACK_COMPLETE).
  AWS allows only ONE OIDC provider per issuer URL — we must reference it via data source, cannot create a new one.
  Our CI roles attach to the same provider with different trust conditions.
- **Auth0**: login.javazone.no — existing IdP, stays as-is
- **Existing TF state**: `javazone-terraform-state` bucket + `terraform-lock` table — manages SQS only, unrelated to our work
- **Security services**: GuardDuty, Security Hub, Config, Inspector (ECR + Lambda), IAM Access Analyzer and account guardrails (EBS/AMI/SSM block-public-access, EBS default encryption) deployed by our platform (`monitoring/`). CloudTrail not managed by us.

### Coexistence Strategy
**Full separation.** We create our own VPC, ALB, ECS cluster, OIDC provider, state bucket — everything.
No importing, no data source references to existing resources, no mixed ownership.
Existing CDK stacks, EB environments, Default-ALB, and GithubOIDC stack are completely untouched.
Migration happens later per-app at developer's pace — apps move from old ALB/ECS to new platform.

## Essentials

- Terraform organized into local modules under `terraform/platform/` (CI-applied) and `terraform/org/` (human-applied)
- Root `main.tf` in each wires modules together
- Lambda source code in `terraform/lambda-src/{function_name}/`
- Push to `main` → GitHub Actions runs plan → LLM review → apply
- LLM review can block auto-apply on HIGH risk
- No-change plans skip review and apply automatically
- Run `terraform fmt -recursive` and `terraform validate` before committing
- Run tests: `python3 -m unittest discover -s scripts/tests` and `python3 -m unittest discover -s terraform/lambda-src/tests`
- Commit with descriptive messages

## Working Rules

- **Variables, not hardcodes**: Use `var.region`, `var.project`, `var.aws_account_id`, `var.domain`. Never hardcode account IDs, regions, or domains.
- **No inline scripts in CI workflows**: Never embed large shell/Python scripts in workflow YAML. Keep scripts in `scripts/` and reference them. Workflow steps should be short commands that call scripts, not scripts themselves.
- **No manual resource creation**: All AWS resources come from Terraform modules. Never create resources via CLI as a stopgap — fix the module/workflow so it provisions automatically.
- **Clean, generalized implementations**: Always patternize. If something will be used more than once, make it a module, script, or reusable component from the start. No hacky workarounds.
- **IAM least-privilege**: Scope `Resource` to specific ARNs/regions. If `*` is required by AWS, add a comment explaining why.
- **Secrets via SSM**: Webhook URLs and secrets in SSM Parameter Store under `/javabin/`. Lambdas read at runtime via `ssm:GetParameter`. Never in env vars, TF variables, or code.
- **Tags via provider**: All resources get `default_tags` from `providers.tf`. Don't manually add tags that are already in defaults. 5 static tags (team, service, repo, environment, managed-by) are set at deploy time; 2 dynamic tags (created-by, commit) are added by the resource-tagger Lambda via EventBridge.
- **Team-prefixed naming**: App resources use `{team}-{service}` naming. The permission boundary enforces this — apps can only create resources whose names start with their team prefix.
- **Permission boundary is human-applied**: The boundary lives in `terraform/org/boundary.tf` and is applied manually (not via CI) because its self-protection prevents CI from modifying it.
- **Pattern matching, not lists**: When categorizing AWS services, use keyword matching. Don't hardcode service name lists.
- **No `.zip` files in git**: Lambda zips are build artifacts from `archive_file`. They're in `.gitignore`.
- **Terraform-first**: Everything lives in Terraform from the first resource. No "set up manually, migrate later." Only exception: bootstrap script for state bucket.

## File Reference

### Root
| File | What |
|------|------|
| `CLAUDE.md` | This file — project overview and agent instructions |
| `.gitignore` | Terraform state files, .terraform/, *.tfplan |

### Documentation
| File | What |
|------|------|
| `docs/platform-modules.md` | Platform sub-module architecture and resources |
| `docs/lambda-functions.md` | Lambda functions: triggers, SSM params, env vars |
| `docs/ci-workflow.md` | Platform CI pipeline: plan → review → apply flow |
| `docs/reusable-modules.md` | App modules: inputs/outputs, app.yaml schema |
| `docs/reusable-workflows.md` | App CI workflows: javabin.yml orchestration |
| `docs/app-yaml-reference.md` | app.yaml schema and field reference |
| `docs/bootstrap-runbook.md` | State backend bootstrap procedure |
| `docs/org-runbook.md` | AWS Organizations setup procedure |
| `docs/cognito-google-setup.md` | Cognito + Google Workspace IdP setup |
| `docs/apply-gate.md` | Apply gate: credential broker, HMAC overrides, security model |

### Terraform — Platform (CI-applied)
```
terraform/platform/
  main.tf          Module wiring
  providers.tf     AWS provider, default_tags from CI env vars
  variables.tf     Root variables
  outputs.tf       Exported values
  backend.tf       S3 + DynamoDB state backend
  networking/      VPC, subnets, NAT, SGs
  ingress/         ALB, ACM, Route53
  iam/             GitHub OIDC, CI roles, permission boundary
  compute/         ECS cluster, ECR base config
  monitoring/      SNS, EventBridge, Config, GuardDuty, Security Hub
  lambdas/         slack-alert, cost-report, daily-cost-check, compliance-reporter, resource-tagger, budget-enforcer, override-cleanup, team-provisioner, apply-gate, securityhub-summary, password-set, ci-broker, app-auth-sync, plan-review, app-secret-rollout
  identity/        Cognito user pools (internal + external), auth-claims trigger, auth memberships table. Identity Center is in terraform/org/
```

### Terraform — Org (human-applied, no CI)
```
terraform/org/
  main.tf              AWS Organizations, SCPs
  identity-center.tf   IAM Identity Center, permission sets, ABAC (team attribute from SAML)
  boundary.tf          Permission boundary policy (human-applied, self-protecting)
  cloudtrail.tf        CloudTrail trail + S3 bucket
  providers.tf         Provider config
  variables.tf         Variables
  backend.tf           Separate state key
```

### Terraform — State (bootstrapped)
```
terraform/state/
  main.tf          S3 bucket, DynamoDB tables, plan artifact bucket
  providers.tf     Provider config
  backend.tf       Starts local, migrates to S3
```

### Reusable Modules (app repos source via `git::` URLs)
| Module | What |
|--------|------|
| `terraform/modules/platform-data/` | Read-only data sources for shared infra |
| `terraform/modules/ecr-repo/` | ECR repository with lifecycle policy |
| `terraform/modules/service-routing/` | ALB target group + listener rule + DNS; optional per-path ALB sign-in (`routing.login`) |
| `terraform/modules/service-role/` | ECS task IAM role with composable policies |
| `terraform/modules/ecs-service/` | ECS Fargate service |
| `terraform/modules/service-bucket/` | S3 bucket with IAM policy output |
| `terraform/modules/service-database/` | DynamoDB table with IAM policy output |
| `terraform/modules/service-secret/` | SSM Parameter Store SecureString with IAM policy output |
| `terraform/modules/service-queue/` | SQS queue + DLQ with IAM policy output |
| `terraform/modules/service-alarm/` | CloudWatch alarms for ECS service |
| `terraform/modules/cognito-app-client/` | Requests a Cognito app client via SSM; the `app-auth-sync` Lambda creates it |

### GitHub Actions Workflows
```
.github/workflows/
  platform-ci.yml      Platform's own CI: plan → review → apply
  javabin.yml          Unified entrypoint (app repos call this)
  detect.yml           Detect repo contents
  build-jvm.yml        Maven or Gradle build + test
  build-ts.yml         pnpm install + build
  docker-build.yml     Docker BuildKit build + ECR push
  tf-plan.yml          Terraform plan + S3 artifact upload
  tf-apply.yml         SHA verify + apply via project role
  eb-deploy.yml        Elastic Beanstalk deploy (transition)
  ecs-deploy.yml       ECS task definition update
  approve-override.yml Risk gate override (board members)
  expand-terraform.yml Validate app.yaml, generate terraform/, commit it
```

> **Note:** Plan + LLM review are combined inline in `platform-ci.yml` for the platform repo
> and in `tf-plan.yml` for app repos (no separate `plan-review.yml` or `commit-terraform.yml`).

### Lambda Functions
| Function | Purpose |
|----------|---------|
| `slack-alert` | SNS → Slack with LLM risk analysis |
| `cost-report` | Weekly cost breakdown with LLM narrative |
| `daily-cost-check` | Daily spike detection (silent if no spikes) |
| `compliance-reporter` | Reports untagged resources to Slack (no auto-fix) |
| `override-cleanup` | Hourly cleanup of stale SSM override tokens |
| `team-provisioner` | Syncs Google Groups, GitHub teams, AWS Budgets from registry team YAML; Atlassian groups from `groups/atlassian.yaml` |
| `securityhub-summary` | Weekly Security Hub findings summary (Monday 08:00 UTC) |
| `password-set` | Self-service password-set for new hero accounts (ALB only: password.javazone.no) |
| `budget-enforcer` | Scales ECS services to zero when team exceeds 200% budget |
| `resource-tagger` | EventBridge-triggered, auto-tags created-by + commit on new resources |
| `ci-broker` | Verifies the job's GitHub OIDC token (repo from signed claims), checks repo ∈ app.yaml team, vends short-lived team role credentials |
| `app-auth-sync` | Creates/deletes Cognito app clients requested by app.yaml `auth:` and `routing.login` (SSM request → client + credentials + allowed groups) |
| `auth-claims` | Pre-token trigger: registry groups → `cognito:groups` (internal pool only), enforces per-client allowed groups; runs as `auth-claims-external` on the external pool (in `identity/`) |
| `plan-review` | LLM review of app Terraform plans with platform credentials; writes `risk.json` (invoked by app tf-plan) |
| `app-secret-rollout` | Restarts `{team}-{app}` when one of its secrets in `/javabin/apps/` changes (ECS reads secrets only at task start) |

### Scripts
| Script | What |
|--------|------|
| `scripts/bootstrap.sh` | One-time: create state bucket + lock tables |
| `scripts/generate-modules.py` | CI: validates app.yaml, generates fmt-clean module blocks into terraform/ (tests: `scripts/tests/`) |
| `scripts/registry.py` | Module sources + boilerplate templates for generate-modules.py |
| `scripts/resolve-tool-version.sh` | CI: resolve node/java version from the app repo's version files |
| `scripts/sync-registered-teams.py` | CI: registry `teams/*.yaml` → `registered-teams.auto.tfvars` (needs App token) |
| `scripts/github-oidc-token.sh` | CI: mint a GitHub OIDC token for the broker / apply gate |
| `scripts/provision-teams.py` | CI: fetch team YAMLs from registry, invoke team-provisioner Lambda |
| `scripts/provision-groups.py` | CI: resolve members.yaml + access.yaml, invoke team-provisioner Lambda |
| `scripts/provision-atlassian.py` | CI: resolve atlassian.yaml to java.no emails, invoke team-provisioner `sync_atlassian` (report-only unless `atlassian_sync_apply`) |
| `scripts/sync-members.py` | CI: sync approved heroes from Google Sheets into members.yaml |
| `scripts/review-plan.py` | CI: LLM plan review via Bedrock (platform CI; also bundled into the plan-review Lambda) |
| `scripts/invoke-plan-review.sh` | CI: app tf-plan → plan-review Lambda |
| `scripts/notify-slack.py` | CI: generic Slack webhook notification |
| `scripts/invoke-apply-gate.sh` | CI: invoke gate Lambda for apply credentials |
| `scripts/run-plan.sh` | CI: terraform plan with exit code handling |
| `scripts/upload-plan.sh` | CI: upload plan artifact to S3 |
| `scripts/auth-doctor.py` | CI (after app apply) / local: checks an app's Cognito sign-in end to end from auth-status + HTTP probes (tests: `scripts/tests/`) |
| `scripts/reconcile-app-auth.sh` | CI (after platform apply): queue an app-auth-sync reconcile |

## Naming

| Type | Pattern |
|------|---------|
| Platform resources | `javabin-{purpose}` |
| App resources | `{team}-{service}` or `{team}-{service}-{suffix}` |
| IAM CI roles | `javabin-ci-{purpose}` |
| App IAM roles | `{team}-{service}` |
| Lambdas | `javabin-{function}` |
| S3 buckets (platform) | `javabin-{purpose}-{account_id}` |
| S3 buckets (app) | `{team}-{purpose}-{account_id}` |
| SSM params (platform) | `/javabin/{namespace}/{name}` |
| SSM params (app) | `/javabin/apps/{team}/{service}/{name}` |
| ECS cluster | `javabin-platform` |
| ECS execution roles | `javabin-ecs-exec-{team}` (platform-owned, reads only `/javabin/apps/{team}/*`) |
| SSO team guardrails | `javabin-team-{team}-guardrails` (developer boundary with the team written in, for `javabin-team-{team}` permission sets) |
| ECR repos | `{team}-{service}` |
| SNS topics | `javabin-{alerts,security,budget-enforcement}` |
| Log groups | `/ecs/{team}/{service}` |

## Alert Routing

```
EventBridge ──► javabin-security SNS ──► slack-alert Lambda:
  Security Hub findings (NEW only) ──► #platform-security-alerts
  GuardDuty findings              ──► #platform-security-alerts
  IAM / resource / login events   ──► #javabin-infra-alerts
Cost Anomaly ──► javabin-alerts SNS ──► slack-alert Lambda ──► #javabin-cost-alerts

Scheduled:
  Monday 08:00 UTC ──► cost-report ──► #javabin-cost-alerts
  Monday 08:00 UTC ──► securityhub-summary ──► #platform-security-alerts
  Daily 08:00 UTC  ──► daily-cost-check ──► #javabin-cost-alerts (only on spikes)

EventBridge (Create/Run) ──► compliance-reporter (report to Slack, no auto-fix)
  Hourly             ──► override-cleanup (delete stale SSM override tokens)
Registry merge ──► team-provisioner (Google/GitHub/Budget/Cognito/Identity Center sync + member provisioning + access group sync)
AWS Budgets (200%) ──► budget-enforcer Lambda ──► ECS scale-to-zero + #javabin-cost-alerts
EventBridge (Create/Run) ──► resource-tagger Lambda ──► Tag created-by + commit
```

## SSM Parameters

All parameters are in `eu-central-1`. Use `--profile javabin --region eu-central-1` via CLI.

| Path | Type | Used By |
|------|------|---------|
| `/javabin/slack/platform-resource-alerts-webhook` | SecureString | slack-alert, compliance-reporter, platform-ci |
| `/javabin/slack/platform-security-alerts-webhook` | SecureString | slack-alert (Security Hub + GuardDuty), securityhub-summary |
| `/javabin/slack/platform-cost-alerts-webhook` | String | slack-alert (cost), cost-report, daily-cost-check |
| `/javabin/slack/platform-override-alerts-webhook` | SecureString | tf-apply (block notification), approve-override |
| `/javabin/platform/google-admin-sa` | SecureString | team-provisioner (GCP SA JSON key, domain-wide delegation) |
| `/javabin/platform/google-admin-email` | String | team-provisioner (admin email for Google Admin SDK impersonation) |
| `/javabin/platform/github-app-id` | SecureString | team-provisioner (GitHub App ID) |
| `/javabin/platform/github-app-key` | SecureString | team-provisioner (GitHub App private key) |
| `/javabin/platform/github-app-client-secret` | SecureString | team-provisioner (GitHub App client secret) |
| `/javabin/platform/atlassian-org-id` | SecureString | team-provisioner (Atlassian organisation ID) |
| `/javabin/platform/atlassian-api-key` | SecureString | team-provisioner (Atlassian org admin API key, unscoped — full org admin) |
| `/javabin/platform-overrides/{repo}/{sha}` | SecureString | Risk gate override tokens (single-use) |
| `/javabin/apps/{team}/{app}/auth` | String | app.yaml `auth:` request (written by app TF, read by app-auth-sync) |
| `/javabin/apps/{team}/{app}/cognito-client-{id,secret,identity-provider}` | String / SecureString / String | Written by app-auth-sync (only on change; a change restarts the app), read by ECS execution role |
| `/javabin/apps/{team}/{app}/auth-status` | String (JSON) | Written by app-auth-sync, read by the team and `scripts/auth-doctor.py` |
| `/javabin/platform/google-oauth/internal/{client-id,client-secret}` | String / SecureString | Internal pool's Google OIDC IdP (identity TF; IdP is created only when both exist) |

## GCP Connection

**GCP Org:** java.no
**GCP Project:** `javabin-platform`
**Purpose:** Service account with domain-wide delegation for Google Admin SDK

A GCP service account in the `javabin-platform` project has domain-wide delegation configured
in Google Workspace Admin. The team-provisioner Lambda uses it to manage Google Groups
(create groups, sync membership) by impersonating the admin email stored in SSM.
The SA JSON key is at `/javabin/platform/google-admin-sa`, the impersonation target at
`/javabin/platform/google-admin-email`.

## Work Packages

### Task Status

| ID | Task | Status |
|----|------|--------|
| 0a | AWS Discovery | **Done** |
| 0b | Bootstrap State Backend | **Done** — S3 backend live |
| 0c | Organizations + Permission Boundary | **Done** — org enabled, boundary deployed, SCP deferred |
| 1 | Identity (Google + Identity Center + Cognito) | **Deployed** — GCP SA with domain-wide delegation, Identity Center with ABAC + 3 permission sets in `terraform/org/`. Google Workspace SAML IdP for SSO (auto-provisions users, groups synced via CI/team-provisioner). Cognito pool TF exists but not yet applied (needs Google OAuth client). |
| 2a | Networking | **Deployed** — VPC, subnets, NAT |
| 2b | Ingress | **Deployed** — ALB + ACM cert |
| 2c | IAM / OIDC | **Deployed** — 6 CI roles (infra, infra-plan, per-app, deploy, override-approver, registry), team-prefixed naming + permission boundary |
| 2d | Compute | **Deployed** — ECS cluster + ECR repos |
| 2e | Monitoring | **Deployed** — GuardDuty, Security Hub, Config, SNS |
| 2f | Lambda Functions | **Deployed** — 11 functions (budget-enforcer, resource-tagger, ci-broker added; Google/GitHub/Budget/Cognito/Identity Center sync live) |
| 2g | Platform CI | **Done** — plan → LLM review → apply pipeline working |
| 3a | Reusable Terraform Modules | **Code done** — 12 modules in repo |
| 3b | GitHub Actions Workflows | **Code done** — 14 reusable workflows |
| 3c | app.yaml Schema + Generation | **Done** — generate-modules.py (strict schema validation, module blocks) + registry.py |
| 3d | Registry Repo | **Working** — repo exists, dispatch uses GitHub App token, team provisioner invoked |
| 3e | javabin CLI | **Code done** — 4 commands (register, init, status, whoami) in javaBin/javabin-cli |
| 3f | CI Images + Supporting Repos | Not started |
| 3g | Tags, Naming, ABAC | **Done** — 5 static + 2 dynamic tags, team-prefixed naming enforced by permission boundary, resource-tagger Lambda for created-by/commit |
| 4 | App Onboarding | **Partially working** — platform-test-app full pipeline passes (plan → review → apply → docker-build), ECS deploy fails on service stabilization |

### Known Issues
- **ECS deploy stabilization**: platform-test-app task registers but service fails health check
- **Cognito**: pools deployed; internal pool's `GoogleSAML` IdP was created outside Terraform. It is replaced by the Terraform-managed Google OIDC IdP at cutover (docs/cognito-google-setup.md), then deleted
- **SSO sessions carry no `team` tag**: the Identity Center ABAC attribute (`${path:enterprise.team}`) is never set on users, so `${aws:PrincipalTag/team}` is empty for humans. Per-team permission sets (`javabin-team-{team}`) therefore use `javabin-team-{team}-guardrails` (the developer boundary rendered with the team name) instead. The ABAC-based `javabin-developer` permission set in `terraform/org` cannot reach team resources
- **App databases share one network path**: service-rds allows ingress from the shared ECS tasks SG, so every team's tasks can reach every team's DB port (password still required). Fix: per-app security groups
- **Team provisioner Lambda**: All sync functions working (Google/GitHub/Budget/Cognito/Identity Center). Password-set flow deployed.
- **`registered_app_repos` manually managed**: Being replaced with team-scoped IAM roles (repo→team resolved via GitHub API at runtime)
- **Cost allocation tags pending activation**: `repo`, `created-by`, `commit` tags need activation in Billing console (requires billing data to appear first)
- **Platform-test-app naming migration**: Existing resources have old `javabin-` prefix names, needs state migration to `{team}-{service}` naming

## Agent Guidelines

When working on any task:
1. Read `CLAUDE.md` and relevant `docs/` files for context
2. Create/update `CLAUDE.md` or doc MDs near the code when detail is too specific for this file
3. Never hardcode secrets — SSM only
4. Never push without the user's confirmation
5. Never implement hacky solutions — generalize
6. Update task status in this file when complete
7. **Always check PR state before pushing to a branch** — run `gh pr view` to verify the PR is still open. The user may have already merged it. Pushing to a merged branch creates orphaned commits.
