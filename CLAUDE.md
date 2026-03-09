# CLAUDE.md — Javabin Platform Infrastructure

Terraform-managed AWS infrastructure for **Javabin** (java.no), the Norwegian Java User Group.
Adapted from the internal-platform reference implementation (Internal/Internal).

## Context

Javabin is a non-profit volunteer org (~4,000 members, ~70 active heroes). It runs JavaZone
(~3,600 attendees) and monthly meetups across Norway. This repo (`javaBin/platform`) manages
all shared AWS infrastructure. App repos manage their own resources via reusable modules
sourced from this repo.

**AWS Account:** 553637109631
**Region:** eu-central-1 (Frankfurt) — confirmed, all existing infra here
**Domain:** javazone.no (Route53 hosted zone, 78 records). java.no DNS not in AWS — TBD.
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
- **Security services**: GuardDuty, Security Hub, and Config deployed by our platform. CloudTrail not managed by us.

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
- Commit with descriptive messages

## Working Rules

- **Variables, not hardcodes**: Use `var.region`, `var.project`, `var.aws_account_id`, `var.domain`. Never hardcode account IDs, regions, or domains.
- **No inline scripts in CI workflows**: Never embed large shell/Python scripts in workflow YAML. Keep scripts in `scripts/` and reference them. Workflow steps should be short commands that call scripts, not scripts themselves.
- **No manual resource creation**: All AWS resources come from Terraform modules. Never create resources via CLI as a stopgap — fix the module/workflow so it provisions automatically.
- **Clean, generalized implementations**: Always patternize. If something will be used more than once, make it a module, script, or reusable component from the start. No hacky workarounds.
- **IAM least-privilege**: Scope `Resource` to specific ARNs/regions. If `*` is required by AWS, add a comment explaining why.
- **Secrets via SSM**: Webhook URLs and secrets in SSM Parameter Store under `/javabin/`. Lambdas read at runtime via `ssm:GetParameter`. Never in env vars, TF variables, or code.
- **Tags via provider**: All resources get `default_tags` from `providers.tf`. Don't manually add tags that are already in defaults.
- **Pattern matching, not lists**: When categorizing AWS services, use keyword matching. Don't hardcode service name lists.
- **No `.zip` files in git**: Lambda zips are build artifacts from `archive_file`. They're in `.gitignore`.
- **Terraform-first**: Everything lives in Terraform from the first resource. No "set up manually, migrate later." Only exception: bootstrap script for state bucket.

## Key Differences from Reference (internal-platform)

| Reference (Internal) | Javabin adaptation |
|---|---|
| GitLab CI / GitLab OIDC | GitHub Actions / GitHub OIDC |
| `ci-runner-*` roles | `javabin-ci-*` roles |
| `gl__{project}__{sha}__{pipeline}` session name | OIDC sub claim is the session name automatically |
| Single repo/account | Multi-repo golden path via `javaBin/registry` |
| ci-authorizer Lambda (custom OIDC) | GitHub environment protection + `job_workflow_ref` conditions |
| `internal/internal/internal/` namespace | `javaBin/` GitHub org |
| `*.ai.internal.com` | `*.javazone.no` (java.no DNS not in Route53) |
| eu-central-1 | eu-central-1 (same — existing infra is here) |
| Bedrock eu.anthropic.claude-haiku-4-5-20251001-v1:0 | Same model, available in eu-central-1 (all models available, no per-model access needed) |
| ECS cluster: ai-platform | ECS cluster: javabin-platform |
| Slack channels: ai-platform-* | Slack channels: javabin-infra-*, javabin-cost-* |
| `.internal/app.yaml` | `app.yaml` (repo root) |
| No Identity Center | IAM Identity Center + Google Workspace SAML |
| No Cognito | Internal + External Cognito User Pools |
| No registry repo | `javaBin/registry` for team registration |
| SNS topics: ai-platform-* | SNS topics: javabin-* |
| SSM: /ai-platform/* | SSM: /javabin/* |

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
  lambdas/         slack-alert, cost-report, daily-cost-check, compliance-reporter, override-cleanup, team-provisioner, apply-gate
  identity/        Cognito user pools (internal + external). Identity Center is in terraform/org/
```

### Terraform — Org (human-applied, no CI)
```
terraform/org/
  main.tf              AWS Organizations, SCPs
  identity-center.tf   IAM Identity Center, permission sets, ABAC (team attribute from SAML)
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
| `terraform/modules/service-routing/` | ALB target group + listener rule + DNS |
| `terraform/modules/service-role/` | ECS task IAM role with composable policies |
| `terraform/modules/ecs-service/` | ECS Fargate service |
| `terraform/modules/service-bucket/` | S3 bucket with IAM policy output |
| `terraform/modules/service-database/` | DynamoDB table with IAM policy output |
| `terraform/modules/service-secret/` | Secrets Manager secret with IAM policy output |
| `terraform/modules/service-queue/` | SQS queue + DLQ with IAM policy output |
| `terraform/modules/service-alarm/` | CloudWatch alarms for ECS service |
| ~~`terraform/modules/app-stack/`~~ | Removed — replaced by `scripts/expand-modules.py` + `scripts/registry.py` |
| `terraform/modules/cognito-app-client/` | Cognito app client registration (code exists, no pools deployed yet) |

### GitHub Actions Workflows
```
.github/workflows/
  platform-ci.yml      Platform's own CI: plan → review → apply
  javabin.yml          Unified entrypoint (app repos call this)
  detect.yml           Detect repo contents
  build-jvm.yml        Maven build + test
  build-ts.yml         pnpm install + build
  docker-build.yml     Docker BuildKit build + ECR push
  tf-plan.yml          Terraform plan + S3 artifact upload
  tf-apply.yml         SHA verify + apply via project role
  eb-deploy.yml        Elastic Beanstalk deploy (transition)
  ecs-deploy.yml       ECS task definition update
  plan-review.yml      LLM risk + cost analysis
  approve-override.yml Risk gate override (board members)
  provision-app.yml    Team provisioning triggered from registry dispatch
  commit-terraform.yml Commit generated TF files back to app repos
```

### Lambda Functions
| Function | Purpose |
|----------|---------|
| `slack-alert` | SNS → Slack with LLM risk analysis |
| `cost-report` | Weekly cost breakdown with LLM narrative |
| `daily-cost-check` | Daily spike detection (silent if no spikes) |
| `compliance-reporter` | Reports untagged resources to Slack (no auto-fix) |
| `override-cleanup` | Hourly cleanup of stale SSM override tokens |
| `team-provisioner` | Syncs Google Groups, GitHub teams, AWS Budgets from registry team YAML |

### Scripts
| Script | What |
|--------|------|
| `scripts/bootstrap.sh` | One-time: create state bucket + lock tables |
| `scripts/expand-modules.py` | CI: reads app.yaml + module sources, generates expanded .tf files |
| `scripts/registry.py` | Module registry — maps app.yaml sections to platform modules |
| `scripts/provision-teams.py` | CI: fetch team YAMLs from registry, invoke team-provisioner Lambda |
| `scripts/review-plan.py` | CI: LLM plan review via Bedrock |
| `scripts/notify-slack.py` | CI: generic Slack webhook notification |
| `scripts/invoke-apply-gate.sh` | CI: invoke gate Lambda for apply credentials |
| `scripts/run-plan.sh` | CI: terraform plan with exit code handling |
| `scripts/upload-plan.sh` | CI: upload plan artifact to S3 |

## Naming

| Type | Pattern |
|------|---------|
| Resources | `javabin-{purpose}` |
| IAM roles | `javabin-ci-{purpose}` (CI), `javabin-{service}` (runtime) |
| Lambdas | `javabin-{function}` |
| S3 buckets | `javabin-{purpose}-{account_id}` |
| SSM params | `/javabin/{namespace}/{name}` |
| ECS cluster | `javabin-platform` |
| ECR repos | `{service-name}` |
| SNS topics | `javabin-{alerts,security}` |

## Alert Routing

```
EventBridge ──► javabin-security SNS ──► slack-alert Lambda ──► #javabin-infra-alerts
GuardDuty   ──► Security Hub ──► SNS ──► slack-alert Lambda ──► #javabin-infra-alerts
Cost Anomaly ──► javabin-alerts SNS ──► slack-alert Lambda ──► #javabin-cost-alerts

Scheduled:
  Monday 08:00 UTC ──► cost-report ──► #javabin-cost-alerts
  Daily 08:00 UTC  ──► daily-cost-check ──► #javabin-cost-alerts (only on spikes)

EventBridge (Create/Run) ──► compliance-reporter (report to Slack, no auto-fix)
  Hourly             ──► override-cleanup (delete stale SSM override tokens)
Registry merge ──► team-provisioner (STUB — not yet implemented)
```

## SSM Parameters

All parameters are in `eu-central-1`. Use `--profile javabin --region eu-central-1` via CLI.

| Path | Type | Used By |
|------|------|---------|
| `/javabin/slack/platform-resource-alerts-webhook` | SecureString | slack-alert, compliance-reporter, platform-ci |
| `/javabin/slack/platform-cost-alerts-webhook` | String | slack-alert (cost), cost-report, daily-cost-check |
| `/javabin/slack/platform-override-alerts-webhook` | SecureString | tf-apply (block notification), approve-override |
| `/javabin/platform/google-admin-sa` | SecureString | team-provisioner (GCP SA JSON key, domain-wide delegation) |
| `/javabin/platform/google-admin-email` | String | team-provisioner (admin email for Google Admin SDK impersonation) |
| `/javabin/platform/github-app-id` | SecureString | team-provisioner (GitHub App ID) |
| `/javabin/platform/github-app-key` | SecureString | team-provisioner (GitHub App private key) |
| `/javabin/platform/github-app-client-secret` | SecureString | team-provisioner (GitHub App client secret) |
| `/javabin/platform-overrides/{repo}/{sha}` | SecureString | Risk gate override tokens (single-use) |
| `/javabin/platform-apps/{name}/*` | varies | Per-app secrets (Cognito clients, etc. — future) |

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
| 1 | Identity (Google + Identity Center + Cognito) | **Deployed** — GCP SA with domain-wide delegation, Identity Center with ABAC + 3 permission sets in `terraform/org/`. Cognito pool TF exists but not yet applied (needs Google OAuth client). |
| 2a | Networking | **Deployed** — VPC, subnets, NAT |
| 2b | Ingress | **Deployed** — ALB + ACM cert |
| 2c | IAM / OIDC | **Deployed** — 5 CI roles (infra, per-app, deploy, override-approver, registry) |
| 2d | Compute | **Deployed** — ECS cluster + ECR repos |
| 2e | Monitoring | **Deployed** — GuardDuty, Security Hub, Config, SNS |
| 2f | Lambda Functions | **Deployed** — 6 working (Google/GitHub/Budget/Cognito/Identity Center sync live) |
| 2g | Platform CI | **Done** — plan → LLM review → apply pipeline working |
| 3a | Reusable Terraform Modules | **Code done** — 12 modules in repo |
| 3b | GitHub Actions Workflows | **Code done** — 14 reusable workflows |
| 3c | app.yaml Schema + Generation | **Done** — expand-modules.py + registry.py (expanded raw resources) |
| 3d | Registry Repo | **Working** — repo exists, dispatch uses GitHub App token, team provisioner invoked |
| 3e | javabin CLI | **Code done** — 4 commands (register, init, status, whoami) in javaBin/javabin-cli |
| 3f | CI Images + Supporting Repos | Not started |
| 4 | App Onboarding | **Partially working** — platform-test-app full pipeline passes (plan → review → apply → docker-build), ECS deploy fails on service stabilization |

### Known Issues
- **ECS deploy stabilization**: platform-test-app task registers but service fails health check
- **Cognito pools not yet applied**: TF exists but needs Google OAuth client credentials
- **Team provisioner Lambda**: Google/GitHub/Budget sync working. Cognito and Identity Center sync functions are stubs — need implementation to create groups and assign members
- **`registered_app_repos` manually managed**: Being replaced with team-scoped IAM roles (repo→team resolved via GitHub API at runtime)

## Agent Guidelines

When working on any task:
1. Read `CLAUDE.md` and relevant `docs/` files for context
2. Create/update `CLAUDE.md` or doc MDs near the code when detail is too specific for this file
3. Never hardcode secrets — SSM only
4. Never push without the user's confirmation
5. Never implement hacky solutions — generalize
6. Update task status in this file when complete
