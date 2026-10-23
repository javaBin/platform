# Reusable GitHub Actions Workflows

Workflows under `.github/workflows/`. App repos call `javabin.yml` for the full pipeline, or individual workflows for custom control.

## Quick Start — App Repo

Create `.github/workflows/deploy.yml`:
```yaml
name: Deploy
on:
  push:
    branches: [main]
  pull_request:
  workflow_dispatch:

permissions:
  id-token: write       # AWS access via OIDC
  contents: write       # expand commits generated Terraform back to the branch
  pull-requests: write  # plan + review comment on PRs

jobs:
  javabin:
    uses: javaBin/platform/.github/workflows/javabin.yml@main
```

The pipeline detects your repo contents and runs the right jobs. No repository or organization secrets are needed: AWS access is OIDC, and the platform scripts are read from this public repo with your own `GITHUB_TOKEN`.

### Onboarding prerequisites

| Prerequisite | Why | How |
|---|---|---|
| Team exists in `javaBin/registry` (`teams/<team>.yaml`) | Creates the GitHub team; platform CI then creates the team's CI roles `javabin-ci-team-<team>` and `javabin-ci-deploy-<team>` | `javabin register-team`, merge the PR |
| Repo belongs to the GitHub team named in `app.yaml` `team:` | The CI broker verifies repo → team before vending team credentials | `gh api -X PUT orgs/javaBin/teams/<team>/repos/javaBin/<repo> -f permission=push` |
| `app.yaml` at repo root | Declares the infrastructure | See [app-yaml-reference.md](app-yaml-reference.md) |

A repo may belong to several GitHub teams; the one named in `app.yaml` is used.

## Pipeline Flow

```
detect ──┬─ expand ──── tf-plan ── tf-apply ──┐
         ├─ build-jvm ─────────────────────────┼─ docker-build ── ecs-deploy
         └─ build-ts ──────────────────────────┘
```

- **PRs:** detect → build → expand (commits generated TF to the PR branch) → tf-plan (plan + LLM review) → PR comment
- **Main:** detect → build → expand → tf-plan → tf-apply → docker-build → ecs-deploy

## Workflow Reference

### javabin.yml (orchestrator)

Unified entrypoint. Calls all other workflows via the nested reusable workflow pattern. OIDC `job_workflow_ref` resolves to the innermost workflow.

| Input | Default |
|-------|---------|
| `node_version` | read from `.nvmrc` / `.node-version` / `.tool-versions` / `mise.toml`, else 22 |
| `pnpm_version` | `packageManager` in `package.json` |
| `java_version` | read from `.java-version` / `.tool-versions` / `mise.toml`, else 21 |

### detect.yml

Checks for: `app.yaml`, `terraform/`, `Dockerfile`, `pom.xml`, `build.gradle(.kts)`, `pnpm-lock.yaml`, `.elasticbeanstalk/`. Reads `name` and `team` from `app.yaml`.

**Outputs:** `has_yaml`, `has_tf`, `has_docker`, `has_maven`, `has_gradle`, `has_pnpm`, `has_eb`, `app_name`, `app_team`

### build-jvm.yml

Maven (`mvn verify`) or Gradle (`./gradlew build`) build + test. Uploads test reports.

| Input | Default |
|-------|---------|
| `build_tool` | `maven` (javabin.yml passes `gradle` when there is no `pom.xml`) |
| `java_version` | resolved from version files, else 21 |
| `maven_args` / `gradle_args` | (empty) |

### build-ts.yml

pnpm install + `build` + `test`. `build` and `test` are skipped when `package.json` has no such script.

| Input | Default |
|-------|---------|
| `node_version` | resolved from version files, else 22 |
| `pnpm_version` | `packageManager` in `package.json` |

### expand-terraform.yml

Runs `scripts/generate-modules.py`: validates `app.yaml` (unknown or unsupported fields fail with a message), writes `terraform/` and commits it back to the branch with `scripts/commit-generated-tf.sh`. The push uses the caller's `GITHUB_TOKEN`, so it never triggers another run. On PRs from forks the push is skipped.

### docker-build.yml

Docker BuildKit build + push to ECR repo `{team}-{app_name}`. Uses GitHub Actions cache.

| Tag | When |
|-----|------|
| `sha-{8chars}` | Always |
| `main-{yyyyMMdd-HHmm}` | Main/master branch |
| `v1.2.3` | Git tags matching `v*` |
| `latest` | Main/master branch |

**AWS role:** `javabin-ci-app-broker` (OIDC) → broker Lambda → `javabin-ci-deploy-{team}`
**Outputs:** `image_uri`, `image_tag`

### tf-plan.yml

Plans the committed `terraform/` at the branch head (after expand), uploads the plan to S3 with its SHA256, runs the LLM review (`scripts/review-plan.py`, Bedrock) and posts plan + review to the PR.

**AWS role:** `javabin-ci-app-broker` (OIDC) → broker Lambda → `javabin-ci-team-{team}`
**Outputs:** `has_changes`, `plan_key`, `plan_sha256`, `risk_level`, `planned_sha`

### tf-apply.yml

Checks out the commit tf-plan planned, verifies the plan SHA256 and asks the apply-gate Lambda for credentials (HIGH/FAILED risk needs a board-member override), then applies.

**AWS role:** `javabin-ci-apply-gate` (OIDC) → apply-gate Lambda → `javabin-ci-team-{team}`

### ecs-deploy.yml

Forces a new deployment of ECS service `{team}-{app_name}` and waits for it to stabilize. The service has a deployment circuit breaker, so tasks that never become healthy roll back instead of hanging.

**AWS role:** `javabin-ci-app-broker` (OIDC) → broker Lambda → `javabin-ci-deploy-{team}`

### eb-deploy.yml

Transitional Elastic Beanstalk deploy via EB CLI. Will be removed per-repo when ECS migration is confirmed.

### approve-override.yml

`workflow_dispatch` — only board members can trigger. Writes a signed override for a blocked plan. Posts confirmation to Slack.

**AWS role:** `javabin-ci-override-approver`

## Security Model

- **OIDC `job_workflow_ref`** — the broker and apply-gate roles trust only tokens issued to specific workflow files in `javaBin/platform@main`. App repos cannot write workflows that assume them.
- **Team credentials via Lambda** — the broker / apply-gate Lambda checks that the repo belongs to the `app.yaml` team (GitHub API, platform App credentials held in SSM) before assuming the team role. App repos never hold GitHub App credentials.
- **Plan integrity** — SHA256 computed at plan time, verified before apply.
- **Risk gate** — LLM review blocks HIGH risk changes. Board member override required.
- **No stored credentials in app repos** — AWS access is OIDC federation; no IAM access keys, no GitHub App secrets.
