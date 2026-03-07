# Task 3c — app.yaml Schema + Terraform Generation

**Status:** Not started
**Phase:** 3 (Golden Path)
**Depends on:** 3a (reusable modules must exist for generation targets)
**Blocks:** Phase 4 (apps use app.yaml)

## Objective

Define the `app.yaml` contract file schema and adapt the `generate-terraform.sh` script
to produce Terraform from it.

## Context

Read `phases/phase-3-golden-path.md` Task 2 for full spec.
Reference: `scripts/generate-terraform.sh` (reads `.piano/app.yaml` — we read `app.yaml`).

## Deliverables

### 1. `docs/app-yaml-reference.md`
Full schema documentation with examples.

### 2. `scripts/generate-terraform.sh` (adapt)
Changes from reference:
- Read `app.yaml` (repo root), not `.piano/app.yaml`
- Generate `platform.tf` with:
  - S3 backend: bucket, key `apps/{repo}/terraform.tfstate`, table `javabin-terraform-app-locks`
  - AWS provider with default tags from CI env vars
  - Module calls using `git::https://github.com/javaBin/platform.git//modules/{name}`
  - Cognito app client module if `auth` field present
- Support `environments` block (generates per-environment configs)
- Compute `.app-yaml-hash` for change detection

### app.yaml Schema
```yaml
name: cake-redux          # required
team: pkom                # required, must match registry/teams/
compute:                  # optional (can be raw TF only)
  cpu: 512
  memory: 1024
  port: 8080
database:                 # optional
  type: postgres
  size: small
auth: internal            # optional: internal | external | both | none
domain: cake-redux.java.no  # optional
budget_alert_nok: 500     # optional, default 1000
environments:             # optional — if omitted, single prod
  prod:
    compute: { cpu: 512, memory: 1024 }
    domain: submit.javazone.no
  dev:
    compute: { cpu: 256, memory: 512 }
    domain: dev.submit.javazone.no
    auto_deploy: true
    budget_alert_nok: 200
```

## Rules
- `platform.tf` has a GENERATED marker — script only overwrites files with this marker
- Developer can add extra `.tf` files alongside platform.tf
- CI validates plan output has required tags on all resources
- `team` field must reference a known team in registry
- If no app.yaml: CI generates only minimal platform.tf (state + provider with tags)
