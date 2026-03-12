# Reusable GitHub Actions Workflows

Workflows under `.github/workflows/`. App repos call `javabin.yml` for the full pipeline, or individual workflows for custom control.

## Quick Start — App Repo

Create `.github/workflows/deploy.yml`:
```yaml
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

That's it. The pipeline auto-detects your repo contents and runs the right jobs.

## Pipeline Flow

```
detect ──► build-jvm ──► docker-build ──► ecs-deploy
       ├─► build-ts  ──┘                     │
       ├─► tf-plan (includes LLM review) ──► tf-apply
       └─► eb-deploy (transitional)
```

- **PRs:** detect → build → tf-plan (plan + review) → PR comments
- **Main:** detect → build → docker-build → tf-plan (plan + review) → tf-apply → ecs-deploy

## Workflow Reference

### javabin.yml (orchestrator)

Unified entrypoint. Calls all other workflows via nested reusable workflow pattern. Determines which jobs to run based on detect outputs. OIDC `job_workflow_ref` resolves to the innermost workflow.

### detect.yml

Checks for: `app.yaml`, `terraform/`, `Dockerfile`, `pom.xml`, `pnpm-lock.yaml`, `.elasticbeanstalk/`, `cdk.json`. Extracts `app_name` from `app.yaml`.

**Outputs:** `has_yaml`, `has_tf`, `has_docker`, `has_maven`, `has_pnpm`, `has_eb`, `has_cdk`, `app_name`

### build-jvm.yml

Maven build + test. Uploads surefire reports.

| Input | Default |
|-------|---------|
| `java_version` | 21 |
| `maven_args` | (empty) |

### build-ts.yml

pnpm install + build + test.

| Input | Default |
|-------|---------|
| `node_version` | 22 |
| `pnpm_version` | 9 |

### docker-build.yml

Docker BuildKit build + ECR push. Uses GitHub Actions cache (zero ECR storage for layers).

| Tag | When |
|-----|------|
| `sha-{8chars}` | Always |
| `main-{yyyyMMdd-HHmm}` | Main/master branch |
| `v1.2.3` | Git tags matching `v*` |
| `latest` | Main/master branch |

**OIDC role:** `javabin-ci-deploy-{repo}`
**Outputs:** `image_uri`, `image_tag`

### tf-plan.yml

Generates `terraform/` from `app.yaml` if present (via `generate-terraform.sh`), runs plan, uploads to S3 with SHA256. Posts plan to PR.

LLM review is now performed inline within this workflow (no separate `plan-review.yml`). After planning, it downloads the plan text and runs `scripts/review-plan.py` via Bedrock Claude Haiku for risk analysis.

**OIDC role:** `javabin-ci-app-{repo}`
**Outputs:** `has_changes`, `plan_key`, `plan_sha256`, `risk_level`

### tf-apply.yml

Verifies plan SHA256, checks risk gate (HIGH → check SSM override token → block if none), applies. Posts block notifications to Slack with link to approve-override workflow.

**OIDC role:** `javabin-ci-deploy-{repo}`
**Environment:** production

### ecs-deploy.yml

Updates ECS task definition with new image, registers new definition, updates service, waits for stability.

**OIDC role:** `javabin-ci-deploy-{repo}`

### eb-deploy.yml

Transitional Elastic Beanstalk deploy via EB CLI. Will be removed per-repo when ECS migration is confirmed.

**OIDC role:** `javabin-ci-deploy-{repo}`

### approve-override.yml

`workflow_dispatch` — only board members can trigger. Writes single-use SSM override token at `/javabin/platform-overrides/{repo}/{sha}`. Posts confirmation to Slack.

**OIDC role:** `javabin-ci-override-approver`
**IAM gate:** `token.actions.githubusercontent.com:actor` must be in `override_approvers` list

## Security Model

- **OIDC `job_workflow_ref`** — IAM trust policies pin to specific workflow files in `javaBin/platform`. App repos cannot write workflows that assume CI roles.
- **Plan integrity** — SHA256 hash computed at plan time, verified before apply.
- **Risk gate** — LLM review blocks HIGH risk changes. Board member override required.
- **Override tokens** — Single-use SSM parameters. Cleaned hourly by override_cleanup Lambda.
- **No stored credentials** — All AWS access via OIDC federation. No IAM access keys.

## OIDC Role Mapping

| Workflow | IAM Role | Permissions |
|----------|----------|-------------|
| tf-plan.yml | `javabin-ci-app-{repo}` | TF plan, Bedrock Converse |
| tf-apply.yml | `javabin-ci-deploy-{repo}` | TF apply, SSM read |
| docker-build.yml | `javabin-ci-deploy-{repo}` | ECR push |
| ecs-deploy.yml | `javabin-ci-deploy-{repo}` | ECS update, PassRole |
| approve-override.yml | `javabin-ci-override-approver` | SSM put overrides |
