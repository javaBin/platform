# Reusable GitHub Actions Workflows

Workflows under `.github/workflows/`. App repos call `javabin.yml` for the full pipeline, or individual workflows for custom control.

## Quick Start — App Repo

Create `.github/workflows/deploy.yml`:
```yaml
name: Deploy
on:
  push:
    branches: [main]    # your default branch: main or master
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
| Team exists in `javaBin/registry` (`teams/<team>.yaml`) | Creates the GitHub team; platform CI then creates the team's CI roles `javabin-ci-plan-<team>`, `javabin-ci-team-<team>` and `javabin-ci-deploy-<team>` | `javabin register-team`, merge the PR |
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
- **Default branch** (push or manual run): detect → build → expand → tf-plan → tf-apply → docker-build → ecs-deploy (runs the image pinned to the digest docker-build pushed; see [ecs-deploy.yml](#ecs-deployyml))

`docker-build` (and so `ecs-deploy`) in a repo with `app.yaml` or `terraform/` runs only after a
successful `tf-plan`, and after `tf-apply` when the plan had changes. A broken `app.yaml` fails
`expand` and nothing is built or deployed, instead of pushing an image to an ECR repository that the
skipped apply never created.

### Concurrency

`javabin.yml` runs one pipeline per repo and ref (group `javabin-pipeline-<repo>-<ref>`). On the
default branch a second run waits for the first one, so two quick merges cannot push generated
Terraform over each other, fight over the state lock or apply a stale saved plan. GitHub keeps at most
one waiting run per group: a third push replaces the waiting run, and the newest run contains all its
commits. A newer push to a pull request cancels the PR's running pipeline. Don't use a group named
`javabin-pipeline-...` in your own `deploy.yml`: caller and called workflow would deadlock.
`tf-apply.yml` also holds a per-repo group of its own for callers outside `javabin.yml`, and plan and
apply wait up to 5 minutes for the state lock.

### Pull requests from forks and Dependabot

These runs get a read-only `GITHUB_TOKEN` and usually no OIDC token. `expand` checks out the PR
merge ref, validates `app.yaml` and skips the push; `tf-plan` skips every AWS step with a notice
("Terraform plan skipped") and only runs `terraform init -backend=false`, `validate` and `fmt -check`.
The plan runs after merge. A Dependabot PR that does get `id-token: write` from `deploy.yml` plans
like any other PR.

Deploy jobs (`tf-apply`, `docker-build`, `ecs-deploy`, `eb-deploy`) run only when
`github.ref` is `refs/heads/<default branch>` (`github.event.repository.default_branch`) and the
event is not a pull request. The full ref is compared, so a tag that happens to be named like the
default branch never deploys. Repos whose default branch is `master` deploy from `master`; there is no
need to rename it. Pushes to other branches build and plan but never apply.

## Workflow Reference

### javabin.yml (orchestrator)

Unified entrypoint. Calls all other workflows via the nested reusable workflow pattern. OIDC `job_workflow_ref` resolves to the innermost workflow.

| Input | Default |
|-------|---------|
| `node_version` | read from `.nvmrc` / `.node-version` / `.tool-versions` / `mise.toml`, else 22 |
| `pnpm_version` | `packageManager` in `package.json` |
| `java_version` | read from `.java-version` / `.tool-versions` / `mise.toml`, else 21 |
| `maven_args` / `gradle_args` | (empty) — extra arguments for `mvn verify` / `gradle build` |
| `test_postgres_image` | (empty) — set to e.g. `postgres:17` to run a PostgreSQL service container during the JVM build |
| `test_postgres_db` / `test_postgres_user` / `test_postgres_password` | `test` / `test` / `test` |

#### Tests that need PostgreSQL

Set `test_postgres_image` and match database, user and password to what your tests expect. The
container listens on `localhost:5432` and is healthy (`pg_isready`) before the build starts. The
build step also gets `PGHOST`, `PGPORT`, `PGDATABASE`, `PGUSER` and `PGPASSWORD`. The password is
for a throwaway CI container, not a secret. Database and user must be plain identifiers (letters,
digits, `_`), and the build fails early otherwise.

```yaml
jobs:
  javabin:
    uses: javaBin/platform/.github/workflows/javabin.yml@main
    with:
      test_postgres_image: postgres:17
      test_postgres_db: myappunit
      test_postgres_user: localdevuser
      test_postgres_password: localdevuser
```

### detect.yml

Checks for: `app.yaml`, `terraform/`, `Dockerfile`, `pom.xml`, `build.gradle(.kts)`, `pnpm-lock.yaml`, `.elasticbeanstalk/`. Reads `name` and `team` from `app.yaml`.

**Outputs:** `has_yaml`, `has_tf`, `has_docker`, `has_maven`, `has_gradle`, `has_pnpm`, `has_eb`, `app_name`, `app_team`

Fails when `app.yaml` exists without `name` (the name is never taken from the repository name).

### build-jvm.yml

Maven (`mvn verify`) or Gradle (`./gradlew build`) build + test. Uploads test reports.

| Input | Default |
|-------|---------|
| `build_tool` | `maven` (javabin.yml passes `gradle` when there is no `pom.xml`) |
| `java_version` | resolved from version files, else 21 |
| `maven_args` / `gradle_args` | (empty) |
| `test_postgres_image` | (empty) = no database; else a PostgreSQL service container on `localhost:5432` |
| `test_postgres_db` / `test_postgres_user` / `test_postgres_password` | `test` |

### build-ts.yml

pnpm install + `build` + `test`. `build` and `test` are skipped when `package.json` has no such script.

| Input | Default |
|-------|---------|
| `node_version` | resolved from version files, else 22 |
| `pnpm_version` | `packageManager` in `package.json` |

### expand-terraform.yml

Runs `scripts/generate-modules.py`: validates `app.yaml` (unknown or unsupported fields fail with a message), writes `terraform/` and commits it back to the branch with `scripts/commit-generated-tf.sh`. The push uses the caller's `GITHUB_TOKEN`, so it never triggers another run. The commit message (`chore(terraform): update generated Terraform from app.yaml`) deliberately has no skip-CI marker: squash merges copy commit messages, and the marker would skip the merged PR's deploy. On PRs from forks the push is skipped.

The push needs `contents: write` and a branch that accepts pushes from GitHub Actions. If it is
rejected, `expand` fails and says why:

- **Protected default branch** (branch protection or a ruleset): change `app.yaml` through pull
  requests. `expand` commits the generated files to the PR branch, so the default branch is up to
  date after merge and needs no push. If it is already out of date (a platform generator change since
  the last run), open a PR with any change from the default branch and merge it.
- **Branch moved** while the run generated: the run for the newer commit regenerates and pushes.
- **Read-only token**: grant `contents: write` in `deploy.yml`.

GitHub starts no workflow for commits pushed with `GITHUB_TOKEN`, so the generated commit on a PR has
no checks of its own (the run that pushed it goes on to plan it). If the repo requires status checks,
pull the branch and push another commit (`git commit --allow-empty`); that run finds nothing to
regenerate and reports its checks on the new head.

### docker-build.yml

Docker BuildKit build + push to ECR repo `{team}-{app_name}`. Uses GitHub Actions cache.

| Tag | When |
|-----|------|
| `sha-{8chars}` | Always |
| `main-{yyyyMMdd-HHmm}` | Default branch (`github.event.repository.default_branch`, whatever it is called; the prefix stays `main-` for the ECR lifecycle rule) |
| `v1.2.3` | Git tags matching `v*` |
| `latest` | Default branch. Nothing deploys `latest`: it only names the newest default-branch build |

**AWS role:** `javabin-ci-app-broker` (OIDC) → broker Lambda → `javabin-ci-deploy-{team}`
**Outputs:** `image_uri` (`{repo}:sha-…`), `image_tag`, `image` (`{repo}@sha256:…`, the pushed digest; what ecs-deploy runs)

### tf-plan.yml

Plans the committed `terraform/` at the branch head (after expand), uploads the plan to S3 with its SHA256, has the `javabin-plan-review` Lambda review it (Bedrock Claude Haiku 5.5, platform credentials; it writes `risk.json`) and posts plan + review to the PR. A review that cannot run after retries is `FAILED`: re-run the workflow, or request an override if it persists. `scripts/check-alb-priorities.py` checks whether a listener rule the plan creates or moves would take an ALB priority another rule already holds (read-only `describe-rules`); on a conflict the plan comment still gets posted, with the conflict at the top, and the last step fails the job.

**AWS role:** `javabin-ci-app-broker` (OIDC) → broker Lambda → `javabin-ci-plan-{team}` (read-only: refresh, state read, state lock, plan upload; tf-plan runs on any branch, so it cannot change resources); the review step uses the broker OIDC role to invoke `javabin-plan-review`
Repos that bring their own `terraform/` without `backend.tf`/`providers.tf` get them from `scripts/ensure-tf-boilerplate.sh`, with state key `apps/<team>/<repo>` and the team the broker verified (not a GitHub API lookup with the run's token). tf-apply generates the same files with the same team.

**Outputs:** `has_changes`, `plan_key`, `plan_sha256`, `risk_level`, `planned_sha`, `team`

### tf-apply.yml

Checks out the commit tf-plan planned, verifies the plan SHA256 and asks the apply-gate Lambda for credentials (HIGH/FAILED risk needs a board-member override), then applies. Before `terraform init` it restores `.terraform.lock.hcl` from the saved plan (Terraform stores the lock file in the plan, and the SHA256 check covers it), so apply installs exactly the provider versions the plan used even though app repos gitignore the lock file and pin the AWS provider only to `~> 5.0`.

**AWS role:** `javabin-ci-apply-gate` (OIDC) → apply-gate Lambda → `javabin-ci-team-{team}`

### ecs-deploy.yml

Deploys the `image` input (docker-build's digest-pinned `image` output) to ECS service `{team}-{app_name}` with `scripts/ecs-deploy.sh`:

1. Copies the family's latest ACTIVE task definition revision (Terraform's newest, or the last one ecs-deploy registered from it) and registers it with the app container's image set to the digest. Every task of the deploy runs exactly this commit's build, also when tf-apply changed the task definition in the same run.
   The revision gets the base revision's tags (or the service's, if the base has none) with `managed-by=ecs-deploy`.
2. Updates the service to that revision, with the desired count from the service tag `javabin:desired-count` (Terraform writes it from `compute.desired_count`; `budget-enforcer` sets it to 0, which keeps the service at 0 tasks until a tf-apply writes it back).
3. Waits until that deployment's `rolloutState` is `COMPLETED` on the new revision (default timeout 12 minutes from the start of the step, inside the broker's 15-minute deploy credentials). A circuit-breaker rollback, a `FAILED` rollout, a timeout or an AWS error while waiting fails the job and prints the service events, the stopped tasks' `stoppedReason`, each container's exit code and reason, and the log group. A deployment replaced by a newer revision of a later run passes with a warning; that run reports the result.

The `ecs-service` module ignores the service's `task_definition` and `desired_count` after creation, so this job, not Terraform, decides what runs. A Terraform change to the task definition (env, secrets, cpu) reaches the service at the next ecs-deploy, which follows tf-apply in the same run; if docker-build fails, it waits for the next green run. A new service is created with 0 tasks, so its first deployment starts here, once the image exists, instead of failing in tf-apply.

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
