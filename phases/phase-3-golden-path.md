# Phase 3 — Golden Path CI, Registry, and CLI

## Agent Brief

You are building the self-service developer experience for Javabin. This includes:
1. The reusable GitHub Actions CI workflows that app repos call
2. The `javaBin/registry` repo and app/team registration system
3. The `javabin` CLI tool
4. Supporting repos (.github org profile, docs, app-template)

This is where the platform becomes usable by developers beyond the infra team.

**Prerequisites:** Phases 0–2 are complete. VPC, ALB, ECS cluster, CI roles, monitoring
stack, Cognito pools, and Identity Center all exist and are CI-managed.

**Repo:** `javaBin/platform` (workflows), plus new repos `javaBin/registry`, `javaBin/javabin-cli`,
`javaBin/.github`, `javaBin/docs`, `javaBin/app-template`

**AWS Region:** eu-west-1 (confirmed)
**Domain:** java.no
**GitHub Org:** javaBin

---

## Context: Reference Implementation CI

The reference used GitLab CI templates (`ci-templates/*.yml`) and a custom OIDC provider
(ci-authorizer Lambda). For Javabin, we use GitHub Actions reusable workflows and
`job_workflow_ref` OIDC conditions instead. The ci-authorizer Lambda is NOT needed.

Key reference files to study (already copied into this repo):
- `ci-templates/app-pipeline.yml` — golden path pipeline
- `ci-templates/docker-build.yml` — Buildah build pattern
- `ci-templates/ecs-deploy.yml` — ECS deploy pattern
- `ci-templates/terraform-app.yml` — Terraform plan/apply for apps
- `ci-templates/plan-review.yml` — LLM review pattern
- `scripts/generate-terraform.sh` — app.yaml → Terraform generation

---

## Task 1: Reusable GitHub Actions Workflows

Create workflows in `.github/workflows/` that app repos call with a single 5-line file.

### The App Repo's Only CI File

Every app repo adds this one file:
```yaml
# .github/workflows/deploy.yml
name: Deploy
on:
  push:
    branches: [main, master]
  pull_request:

jobs:
  javabin:
    uses: javaBin/platform/.github/workflows/javabin.yml@main
    secrets: inherit
```

### Workflow: `javabin.yml` (unified entrypoint)

Orchestrates all downstream workflows based on detection outputs.

```yaml
# Calls detect.yml first, then conditionally calls:
# - build-jvm.yml or build-ts.yml (if has_maven or has_pnpm)
# - docker-build.yml (if has_docker)
# - tf-plan.yml (if has_tf or has_yaml or has_cdk)
# - plan-review.yml (unified LLM review)
# - tf-apply.yml (on main/master, if plan exists)
# - eb-deploy.yml (if has_eb, transition period)
# - ecs-deploy.yml (if ECS task definition exists)
```

### Workflow: `detect.yml`

Runs first, sets boolean outputs for all downstream jobs:
```yaml
outputs:
  has_yaml:     # app.yaml exists
  has_tf:       # .tf files exist (excluding platform.tf)
  has_cdk:      # cdk.json exists
  has_docker:   # Dockerfile exists
  has_maven:    # pom.xml exists
  has_pnpm:     # pnpm-lock.yaml exists
  has_eb:       # .elasticbeanstalk/ directory exists
  has_env_dirs: # infra/prod/ directory exists
```

### Workflow: `build-jvm.yml`

- Maven build + test
- Uses JVM CI image from ECR (eclipse-temurin:21-jdk-alpine base, pre-warmed deps)
- Cache: `actions/cache` on `~/.m2`

### Workflow: `build-ts.yml`

- pnpm install + build
- Uses TypeScript CI image from ECR (node:22-alpine, pnpm via corepack)
- Cache: `actions/cache` on `~/.pnpm-store`

### Workflow: `docker-build.yml`

- Buildah (daemonless) build + push to ECR
- OIDC auth → `javabin-ci-deploy-{repo-name}` role
- Image tags: `sha-{git-sha}`, `main-{yyyyMMdd-HHmm}`, `v1.2.3` (on tag), `latest`
- Docker layer cache: `cache-from/cache-to: type=gha` (GitHub cache, zero ECR cost)
- ECS task definitions always reference `sha-{git-sha}` — never `latest`

### Workflow: `tf-plan.yml`

- OIDC auth → `javabin-ci-app-{repo-name}` role
- If `app.yaml` exists: generate `platform.tf` (backend config, provider with tags, module calls)
- `terraform init`, `validate`, `fmt -check`, `plan -detailed-exitcode`
- Upload plan + SHA256 to S3 (`javabin-ci-plan-artifacts-{account-id}/{run-id}/{repo}/`)
- STS session tags: `project`, `GitHubActor`, `GitHubSHA`, `GitHubRepo`, `GitHubRunID`
- Default tags injected as `TF_VAR_*` from GitHub context

### Workflow: `tf-apply.yml`

- OIDC auth → `javabin-ci-deploy-{repo-name}` role
  (`job_workflow_ref` condition locks this to platform's tf-apply.yml only)
- Download plan from S3, verify SHA256 hash
- Check risk gate: if HIGH, check SSM for override token
  - If no override: exit 1 with Slack message and pre-filled override link
  - If override exists: proceed, then delete the token (single-use)
- `terraform apply -auto-approve plan.tfplan`

### Workflow: `eb-deploy.yml` (transition period)

- For repos with `.elasticbeanstalk/` directory
- OIDC auth → deploy role
- `eb deploy` using existing EB configuration
- Removed from workflow once repo migrates to ECS (developer confirms)

### Workflow: `ecs-deploy.yml`

- Describe current task definition
- Update image to new ECR tag
- Register new task definition revision
- Update ECS service
- Wait for `services-stable`

### Workflow: `plan-review.yml` (unified LLM review + cost analysis)

- Receives outputs from tf-plan and/or cdk-synth
- Calls Bedrock (Claude Haiku) for risk assessment
- Calls shared pricing utility for cost analysis
- Reads project budget from registry YAML
- Posts single PR comment covering risk + cost:
  ```
  ## Platform Review

  Risk:  LOW
  Cost:  +420 NOK/month (from ~180 to ~600 NOK)

  New resources:
    aws_ecs_service.cake-redux        +290 NOK/mo
    aws_rds_instance.cake-redux-db    +130 NOK/mo

  Analysis: Standard ECS + RDS pattern. Within budget.
  ```
- When no PR (direct push): routes review to `#javabin-infra-alerts` Slack

### Workflow: `approve-override.yml`

- workflow_dispatch with inputs: repo, sha, reason
- OIDC auth → `javabin-ci-override-approver` role
  (locked by `job_workflow_ref` + `actor` allowlist)
- Writes SSM parameter: `/javabin/overrides/{repo}/{sha}`
- Posts approval confirmation to Slack

---

## Task 2: `app.yaml` Generation and Terraform Generation

### `app.yaml` schema
```yaml
name: cake-redux
team: pkom
compute:
  cpu: 512
  memory: 1024
  port: 8080
database:          # optional
  type: postgres
  size: small
auth: internal     # internal | external | both | none
domain: cake-redux.java.no
budget_alert_nok: 500
environments:      # optional — if omitted, only prod
  prod:
    compute: { cpu: 512, memory: 1024 }
    domain: submit.javazone.no
  dev:
    compute: { cpu: 256, memory: 512 }
    domain: dev.submit.javazone.no
    auto_deploy: true
    budget_alert_nok: 200
```

### `scripts/generate-terraform.sh`

Adapt from reference `scripts/generate-terraform.sh`. Changes:
- Read `app.yaml` (not `.piano/app.yaml`)
- Generate `platform.tf` with:
  - S3 backend config (bucket, key `apps/{repo-name}/terraform.tfstate`, table `javabin-terraform-app-locks`)
  - AWS provider with default tags from CI env vars
  - Module calls for each resource in app.yaml (using `git::` URLs to this repo's modules)
  - Cognito app client module if `auth` field present
- Generate one per environment if `environments` block exists
- Only overwrite files with GENERATED marker
- Compute `.app-yaml-hash` for change detection

---

## Task 3: `javaBin/registry` Repo

### Structure
```
registry/
  apps/
    cake-redux.yaml
    moresleep.yaml
    submittheforce.yaml
  teams/
    core.yaml
    nettside.yaml
    pkom.yaml
    platform-owners.yaml
  README.md
```

### App Registration YAML
```yaml
# apps/cake-redux.yaml
name: cake-redux
team: pkom
repo: javaBin/cake-redux
existing: true  # existing app, triggers discovery before provisioning
```

### Team Definition YAML
```yaml
# teams/core.yaml
name: core
description: Core backend systems
google_group: team-core@java.no
members:
  - alexander.amiri
  - hero.name
repos:
  - cake-redux
  - moresleep
  - cupcake
aws_budget_nok: 3000
```

### Registry Workflows (in `javaBin/registry/.github/workflows/`)

**`notify.yml`** — PR opened for apps/ or teams/:
- Posts to `#javabin-infra-alerts` Slack: new registration request details + PR link
- Approve/deny happens in GitHub (audit trail)

**`provision.yml`** — on merge to main:
- Sends `repository_dispatch` event to `javaBin/platform`
- Platform receives dispatch and runs provisioning (creates IAM roles, ECR, Budget, etc.)

**`validate.yml`** — on PR:
- Validates YAML schema (app name, team reference exists, repo exists in org)
- Fails PR check if team name not found in `teams/`

### Access Model
- Private repo, visible to org members only
- Any org member can open PR to register app or modify team
- Main branch protected: required reviewer from `@javaBin/platform-owners`
- Merging triggers infrastructure provisioning — developer never touches platform

---

## Task 4: Reusable Terraform Modules

Adapt the modules from `terraform/modules/` for Javabin. Key changes:
- Naming: `ai-platform-` → project variable (usually the app name)
- Domain: `cxense.com` → `java.no`
- Region: configurable
- New module: `cognito-app-client`

### Modules to adapt:
1. `modules/platform-data/` — discover shared infra (VPC, ALB, ECS, Route53)
2. `modules/ecr-repo/` — ECR with lifecycle policy
3. `modules/service-routing/` — ALB target group + listener rule + DNS
4. `modules/service-role/` — ECS task IAM role with composable policies
5. `modules/ecs-service/` — ECS Fargate service
6. `modules/service-bucket/` — S3 bucket
7. `modules/service-database/` — DynamoDB table
8. `modules/service-secret/` — Secrets Manager secret
9. `modules/service-queue/` — SQS queue + DLQ
10. `modules/service-alarm/` — CloudWatch alarms
11. `modules/app-stack/` — Golden path (reads app.yaml, calls all above)
12. `modules/cognito-app-client/` — NEW: Cognito app client registration

### ECR Lifecycle Policy (applied to every repo)
```json
[
  {"rulePriority": 1, "description": "Keep last 5 semver releases",
   "selection": {"tagStatus": "tagged", "tagPrefixList": ["v"], "countType": "imageCountMoreThan", "countNumber": 5}},
  {"rulePriority": 2, "description": "Keep last 3 main builds",
   "selection": {"tagStatus": "tagged", "tagPrefixList": ["main-", "master-"], "countType": "imageCountMoreThan", "countNumber": 3}},
  {"rulePriority": 3, "description": "Expire untagged after 1 day",
   "selection": {"tagStatus": "untagged", "countType": "sinceImagePushed", "countUnit": "days", "countNumber": 1}},
  {"rulePriority": 4, "description": "Expire all other after 7 days",
   "selection": {"tagStatus": "tagged", "countType": "sinceImagePushed", "countUnit": "days", "countNumber": 7}}
]
```

---

## Task 5: Custom CI Images

Store in ECR under `javaBin/platform`, rebuild monthly via scheduled workflow.

### JVM Image
- Base: `eclipse-temurin:21-jdk-alpine`
- Pre-warmed Maven wrapper + common Spring Boot/Ktor dependencies
- Used by: cake-redux, moresleep, cupcake, annual-meeting-membership

### TypeScript Image
- Base: `node:22-alpine`
- pnpm pre-installed via corepack
- Used by: java.no, 2025.javazone.no-native

### Platform Image
- Base: `hashicorp/terraform:1.x`
- AWS CLI, jq, python3 pre-installed
- AWS Terraform provider pre-mirrored (~50MB download avoided)
- `javabin` CLI binary pre-installed
- Used by: all plan/apply jobs

### Rebuild Workflow
`.github/workflows/build-ci-images.yml`:
- Scheduled: monthly (cron)
- Also triggered on changes to `ci-images/` directory
- Builds all three images, pushes to ECR

---

## Task 6: `javabin` CLI (`javaBin/javabin-cli`)

**Language:** Go. Single static binary.

### Commands
- `javabin register` — interactive wizard, opens registration PR against registry
- `javabin status` — AWS spend, ECS services, recent deployments, compliance
- `javabin whoami` — Cognito identity, AWS role, project scope, token expiry

### Authentication
Lazy device flow against internal Cognito User Pool:
- Any command needing identity checks cached token first
- If expired/missing → device flow triggers inline via Cognito + Google
- Token cached in `~/.javabin/token`
- No AWS credentials stored — uses standard credential chain

### Distribution
```bash
brew install javaBin/tap/javabin        # macOS/Linux
curl -fsSL https://cli.java.no/install.sh | sh  # direct
go install github.com/javaBin/javabin-cli@latest # go toolchain
```

### Release Pipeline
GoReleaser in GitHub Actions on semver tags. Publishes to GitHub Releases + homebrew-tap.

### CLI does NOT include
- plan, apply, generate, validate, or any deploy commands
- Those belong exclusively in CI

---

## Task 7: Supporting Repos

### `javaBin/.github`
- `profile/README.md` — public org page. What Javabin is, how to contribute, how to become
  a hero, links to docs/registry/app-template/CLI install.

### `javaBin/docs`
- How the platform works (architecture overview)
- How to register an app (registry PR flow)
- How auth works (internal Cognito, `auth: internal` in app.yaml)
- How to use the javabin CLI (install, commands, examples)
- How to request access

### `javaBin/app-template`
GitHub template repo. Heroes click "Use this template" to start a new project:
- `.github/workflows/deploy.yml` (the five-line entrypoint)
- `app.yaml` with placeholder values and inline comments
- Minimal `Dockerfile`
- `README.md` explaining next steps (register via registry, install CLI)

---

## Task 8: Platform Repo Protection

### Branch Rulesets on main
- Require PRs (no direct push)
- Required reviewer: `@javaBin/platform-owners` (not the PR author)
- Require status checks (plan + LLM review)
- Block force pushes and branch deletion
- Restrict pushers: only `@javaBin/platform-owners`

### CODEOWNERS
```
*                     @javaBin/platform-owners
terraform/org/        @javaBin/board
terraform/platform/iam/       @javaBin/platform-owners
terraform/platform/monitoring/ @javaBin/platform-owners
```

---

## Constraints

- **No ci-authorizer Lambda.** GitHub `job_workflow_ref` replaces the custom OIDC provider.
- **EB support is transitional.** Include it now, remove per-repo as ECS migration completes.
- **Registry is the IAM gate.** Unregistered repos have no IAM role to assume.
- **Direct push support.** Workflows trigger on both PR and push to main. Review goes to
  Slack when no PR exists.
- **GitHub-hosted runners only.** No self-hosted runners (would share Coolify EC2).
- **Costs in NOK** where possible in cost reports and plan reviews.

## Deliverables

1. `.github/workflows/` — all reusable workflows listed above
2. `scripts/generate-terraform.sh` — adapted app.yaml → TF generation
3. `modules/` — all reusable Terraform modules adapted for Javabin
4. `ci-images/` — Dockerfiles for JVM, TypeScript, platform CI images
5. Registry repo structure and workflows (local files, not pushed)
6. CLI repo structure and Go scaffolding (local files, not pushed)
7. Supporting repo content (.github, docs, app-template)
8. CODEOWNERS and branch ruleset documentation

## What Comes Next (DO NOT implement)

Phase 4 onboards existing apps (cake-redux, moresleep, etc.) using this golden path.
