# Task 3f — CI Images + Supporting Repos

**Status:** Not started
**Phase:** 3 (Golden Path)
**Depends on:** 2d (ECR repos for CI images)
**Blocks:** 3b (workflows use CI images)

## Objective

Build custom CI images and create supporting GitHub repos (.github, docs, app-template).

## Context

Read `phases/phase-3-golden-path.md` Tasks 5 and 7 for full spec.

## Deliverables

### CI Images (`ci-images/`)

**JVM Image** (`ci-images/jvm/Dockerfile`):
- Base: `eclipse-temurin:21-jdk-alpine`
- Pre-warmed Maven wrapper + common Spring Boot/Ktor dependencies
- Used by: cake-redux, moresleep, cupcake, annual-meeting-membership

**TypeScript Image** (`ci-images/ts/Dockerfile`):
- Base: `node:22-alpine`
- pnpm pre-installed via corepack
- Used by: java.no, 2025.javazone.no-native

**Platform Image** (`ci-images/platform/Dockerfile`):
- Base: `hashicorp/terraform:1.x`
- AWS CLI, jq, python3
- AWS Terraform provider pre-mirrored
- `javabin` CLI binary
- Used by: all plan/apply jobs

### CI Image Build Workflow
`.github/workflows/build-ci-images.yml`:
- Schedule: monthly (cron)
- Also on changes to `ci-images/`
- Build all three, push to ECR

### Supporting Repos (local structure, not pushed)

**`javaBin/.github`**:
- `profile/README.md` — public org page
- What Javabin is, how to contribute, links to docs/registry/CLI

**`javaBin/docs`**:
- Platform overview
- How to register an app
- How auth works (Cognito)
- CLI guide (install, commands, examples)
- How to request access

**`javaBin/app-template`** (GitHub template repo):
- `.github/workflows/deploy.yml` (5-line entrypoint)
- `app.yaml` with placeholder values + comments
- Minimal `Dockerfile`
- `README.md` with next steps

## Rules
- CI images rebuilt monthly to pick up security patches
- Platform image is highest impact — provider mirror saves ~50MB per run
- GitHub-hosted runners only (no self-hosted)
- Caching: Maven via actions/cache, pnpm via actions/cache, Docker via type=gha
