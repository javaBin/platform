# Task 3b — Reusable GitHub Actions Workflows

**Status:** Not started
**Phase:** 3 (Golden Path)
**Depends on:** 2c (IAM roles must exist), 2g (platform CI must work)
**Blocks:** Phase 4 (apps call these workflows)

## Objective

Create the reusable GitHub Actions workflows that app repos call with a single 5-line file.
This replaces the GitLab CI templates from the reference.

## Context

Read `phases/phase-3-golden-path.md` Task 1 for full spec.
Reference: `ci-templates/*.yml` (GitLab CI — must be completely rewritten for GitHub Actions).

## Deliverables

### `.github/workflows/` (all reusable via `workflow_call`)

| Workflow | Purpose |
|----------|---------|
| `javabin.yml` | Unified entrypoint — orchestrates all below based on detect outputs |
| `detect.yml` | Detect repo contents (app.yaml, .tf, Dockerfile, pom.xml, etc.) |
| `build-jvm.yml` | Maven build + test (JVM CI image) |
| `build-ts.yml` | pnpm install + build (TS CI image) |
| `docker-build.yml` | Buildah build + ECR push |
| `tf-plan.yml` | Terraform plan + S3 artifact upload |
| `tf-apply.yml` | SHA verify + risk gate + apply |
| `eb-deploy.yml` | Elastic Beanstalk deploy (transition period) |
| `ecs-deploy.yml` | ECS task definition update + wait stable |
| `plan-review.yml` | LLM risk + cost analysis (unified review) |
| `approve-override.yml` | Risk gate override (board members only) |

### App Repo's Only CI File
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

### Key Implementation Details

**detect.yml outputs:** has_yaml, has_tf, has_cdk, has_docker, has_maven, has_pnpm, has_eb, has_env_dirs

**docker-build.yml:**
- Image tags: `sha-{git-sha}`, `main-{yyyyMMdd-HHmm}`, `v1.2.3` (on tag), `latest`
- Layer cache: `cache-from/cache-to: type=gha` (zero ECR storage cost)
- ECS task defs reference `sha-{git-sha}` only

**tf-plan.yml:**
- Generates `platform.tf` if `app.yaml` exists
- Uploads plan + SHA256 to `javabin-ci-plan-artifacts-{account}/`
- Injects default tags as TF_VAR_* from GitHub context

**tf-apply.yml:**
- Verifies plan SHA256 before applying
- Risk gate: checks SSM `/javabin/overrides/{repo}/{sha}` if HIGH
- Deletes override token after use (single-use)
- Posts block notification to Slack with pre-filled override link

**plan-review.yml:**
- Calls Bedrock for risk assessment
- Calls shared pricing utility for cost delta
- Reads project budget from registry
- Single PR comment: risk + cost + analysis + budget status
- On direct push (no PR): routes to Slack

**approve-override.yml:**
- workflow_dispatch with inputs: repo, sha, reason
- OIDC → javabin-ci-override-approver role
- Writes SSM `/javabin/overrides/{repo}/{sha}`
- Posts confirmation to Slack

**eb-deploy.yml:**
- For repos with `.elasticbeanstalk/` directory
- Transitional — removed per-repo when ECS confirmed working

## Rules
- All workflows use `workflow_call` (reusable) except approve-override (dispatch)
- OIDC `job_workflow_ref` is the security boundary — app repos can't bypass
- Direct push to main works — review goes to Slack instead of PR comment
- EB support is transitional, not permanent
- GitHub-hosted runners only (ubuntu-latest)
- No self-hosted runners
