# CLAUDE.md — Javabin Platform Infrastructure

Terraform-managed AWS infrastructure for **Javabin** (java.no), the Norwegian Java User Group.
Adapted from the ai-platform-infra reference implementation (Cxense/Piano).

## Context

Javabin is a non-profit volunteer org (~4,000 members, ~70 active heroes). It runs JavaZone
(~3,600 attendees) and monthly meetups across Norway. This repo (`javaBin/platform`) manages
all shared AWS infrastructure. App repos manage their own resources via reusable modules
sourced from this repo.

**AWS Account:** 553637109631
**Region:** eu-central-1 (Frankfurt) — confirmed, all existing infra here
**Domain:** javazone.no (Route53 hosted zone, 78 records). java.no DNS not in AWS — TBD.
**GitHub Org:** github.com/javaBin
**CI/CD:** GitHub Actions (not GitLab — the reference used GitLab)
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
- **Security services**: None enabled (no CloudTrail, GuardDuty, Security Hub, Config)

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
- **IAM least-privilege**: Scope `Resource` to specific ARNs/regions. If `*` is required by AWS, add a comment explaining why.
- **Secrets via SSM**: Webhook URLs and secrets in SSM Parameter Store under `/javabin/`. Lambdas read at runtime via `ssm:GetParameter`. Never in env vars, TF variables, or code.
- **Tags via provider**: All resources get `default_tags` from `providers.tf`. Don't manually add tags that are already in defaults.
- **Pattern matching, not lists**: When categorizing AWS services, use keyword matching. Don't hardcode service name lists.
- **No `.zip` files in git**: Lambda zips are build artifacts from `archive_file`. They're in `.gitignore`.
- **Terraform-first**: Everything lives in Terraform from the first resource. No "set up manually, migrate later." Only exception: bootstrap script for state bucket.

## Key Differences from Reference (ai-platform-infra)

| Reference (Cxense) | Javabin adaptation |
|---|---|
| GitLab CI / GitLab OIDC | GitHub Actions / GitHub OIDC |
| `gitlab-runner-*` roles | `javabin-ci-*` roles |
| `gl__{project}__{sha}__{pipeline}` session name | OIDC sub claim is the session name automatically |
| Single repo/account | Multi-repo golden path via `javaBin/registry` |
| ci-authorizer Lambda (custom OIDC) | GitHub environment protection + `job_workflow_ref` conditions |
| `piano/cxense/dexa/` namespace | `javaBin/` GitHub org |
| `*.ai.cxense.com` | `*.javazone.no` (java.no DNS not in Route53) |
| eu-central-1 | eu-central-1 (same — existing infra is here) |
| Bedrock eu.anthropic.claude-haiku-4-5-20251001-v1:0 | Same model, available in eu-central-1 (all models available, no per-model access needed) |
| ECS cluster: ai-platform | ECS cluster: javabin-platform |
| Slack channels: ai-platform-* | Slack channels: javabin-infra-*, javabin-cost-* |
| `.piano/app.yaml` | `app.yaml` (repo root) |
| No Identity Center | IAM Identity Center + Google Workspace SAML |
| No Cognito | Internal + External Cognito User Pools |
| No registry repo | `javaBin/registry` for app/team registration |
| SNS topics: ai-platform-* | SNS topics: javabin-* |
| SSM: /ai-platform/* | SSM: /javabin/* |

## File Reference

### Root
| File | What |
|------|------|
| `CLAUDE.md` | This file — project overview and agent instructions |
| `PLAN.md` | Full platform plan (all phases, constraints, architecture) |
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
  lambdas/         slack-alert, cost-report, daily-cost-check, compliance-reporter, override-cleanup, team-provisioner
  identity/        IAM Identity Center, Cognito pools (new)
```

### Terraform — Org (human-applied, no CI)
```
terraform/org/
  main.tf          AWS Organizations, SCPs
  providers.tf     Provider config
  variables.tf     Variables
  backend.tf       Separate state key
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
| `terraform/modules/app-stack/` | Golden path — reads `app.yaml`, creates all infra |
| `terraform/modules/cognito-app-client/` | Cognito app client registration (new) |

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
  provision-app.yml    App provisioning triggered from registry
```

### Lambda Functions
| Function | Purpose |
|----------|---------|
| `slack-alert` | SNS → Slack with LLM risk analysis |
| `cost-report` | Weekly cost breakdown with LLM narrative |
| `daily-cost-check` | Daily spike detection (silent if no spikes) |
| `compliance-reporter` | Reports untagged resources to Slack (no auto-fix) |
| `override-cleanup` | Hourly cleanup of stale SSM override tokens |
| `team-provisioner` | Syncs teams across Google, GitHub, Cognito, IAM (stub) |

### Scripts
| Script | What |
|--------|------|
| `scripts/bootstrap.sh` | One-time: create state bucket + lock tables |
| `scripts/generate-terraform.sh` | CI: app.yaml → Terraform files |
| `scripts/review-plan.py` | CI: LLM plan review via Bedrock |
| `scripts/notify-block.py` | CI: post Slack notification when deploy is blocked |

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
Registry merge ──► team-provisioner (Google + GitHub + Cognito + IAM sync)
```

## SSM Parameters

| Path | Used By |
|------|---------|
| `/javabin/slack/platform-resource-alerts-webhook` | slack-alert (security events), compliance-reporter, platform-ci (HIGH risk, drift) |
| `/javabin/slack/platform-cost-alerts-webhook` | slack-alert (cost), cost-report, daily-cost-check |
| `/javabin/slack/platform-override-alerts-webhook` | tf-apply (block notification), approve-override (approval confirmation) |
| `/javabin/platform/google-admin-sa` | team-provisioner (Google Admin SDK) |
| `/javabin/platform/github-app-key` | team-provisioner (GitHub App private key) |
| `/javabin/platform-overrides/{repo}/{sha}` | Risk gate override tokens (single-use) |
| `/javabin/platform-apps/{name}/*` | Per-app secrets (Cognito clients, etc.) |

## Work Packages

See `PLAN.md` for the full plan. Each work package has a task brief in `phases/tasks/`.
Phase-level context is in `phases/phase-{n}-*.md`.

### Dependency Graph
```
0a-discovery ──► 0b-bootstrap ──► 0c-org-and-boundary
                      │                    │
                      ▼                    ▼
                 2a-networking        2c-iam-oidc ──► 2g-ci-handoff
                      │                    │               │
                      ▼                    │               ▼
                 2b-ingress               │          (all subsequent
                      │                    │           via CI pipeline)
                      ▼                    ▼
                 2d-compute          2e-monitoring ──► 2f-lambdas
                      │
                      ▼
                 3a-reusable-modules ──► 3c-app-yaml
                      │                      │
                      ▼                      ▼
                 3b-github-workflows    3d-registry ──► Phase 4
                      │
                 3e-cli (independent)
                 3f-ci-images (independent)

Phase 1 (identity) is blocked on Google Admin access — implement when unblocked.
```

### Task Index

| ID | Task | Status | Brief |
|----|------|--------|-------|
| 0a | AWS Discovery | **Done** | `phases/discovery-results.md` |
| 0b | Bootstrap State Backend | **Done** — bootstrapped, imported, migrated to S3 | `phases/tasks/0b-bootstrap.md` |
| 0c | Organizations + Permission Boundary | **Done** — org enabled, boundary deployed, SCP deferred | `phases/tasks/0c-org-and-boundary.md` |
| 1 | Identity (Google + Identity Center) | Unblocked (Google Admin confirmed) — not started | `phases/phase-1-identity.md` |
| 2a | Networking | **Deployed** — VPC, subnets, NAT live | `phases/tasks/2a-networking.md` |
| 2b | Ingress | **Deployed** — ALB + cert issued | `phases/tasks/2b-ingress.md` |
| 2c | IAM / OIDC | **Deployed** — 4 CI roles + per-app roles | `phases/tasks/2c-iam-oidc.md` |
| 2d | Compute | **Deployed** — ECS cluster + 3 ECR repos | `phases/tasks/2d-compute.md` |
| 2e | Monitoring | **Deployed** — GuardDuty, Security Hub, Config, SNS | `phases/tasks/2e-monitoring.md` |
| 2f | Lambda Functions | **Deployed** — 6 handlers, shared utils, Block Kit | `phases/tasks/2f-lambdas.md` |
| 2g | Platform CI | **Done** — CI working, AWS CLI converse for review | `phases/tasks/2g-ci-handoff.md` |
| 3a | Reusable Terraform Modules | **Code done** — 12 modules adapted | `phases/tasks/3a-reusable-modules.md` |
| 3b | GitHub Actions Workflows | **Code done** — 11 reusable workflows | `phases/tasks/3b-github-workflows.md` |
| 3c | app.yaml Schema + Generation | **Done** — generate-terraform.sh + docs | `phases/tasks/3c-app-yaml.md` |
| 3d | Registry Repo | **Done** — pushed, branch protected | `phases/tasks/3d-registry.md` |
| 3e | javabin CLI | **Code done** — 3 commands (register, status, whoami) | `phases/tasks/3e-cli.md` |
| 3f | CI Images + Supporting Repos | Not started | `phases/tasks/3f-ci-images-and-repos.md` |
| 4 | App Onboarding | **In progress** — platform-test-app registered | `phases/phase-4-onboarding.md` |

## Agent Guidelines

When working on any task:
1. Read the task brief in `phases/tasks/` first
2. Read the parent phase file in `phases/` for broader context
3. Read `PLAN.md` for constraints and architectural decisions
4. Create/update `CLAUDE.md` or doc MDs near the code when detail is too specific for this file
5. Never hardcode secrets — SSM only
6. Never push without Alexander's confirmation
7. Never implement hacky solutions — generalize
8. Update task status in this file when complete
9. Update `PLAN.md` to reflect implementation (replace "what to do" with "what was done")
