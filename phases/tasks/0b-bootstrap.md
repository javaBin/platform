# Task 0b — Bootstrap State Backend

**Status:** Not started
**Phase:** 0 (Foundation)
**Depends on:** 0a (discovery — need account ID and confirmed region)
**Blocks:** All Terraform work

## Objective

Create the S3 state bucket, DynamoDB lock tables, and plan artifact bucket via a bootstrap
script. Then write the `terraform/state/` module to manage these resources, import them,
and migrate state to S3.

## Context

Read `phases/phase-0-foundation.md` for full context on the bootstrap sequence.
Read `phases/discovery-results.md` for the confirmed region and account ID.

**Confirmed decisions:**
- Region: eu-central-1 (Frankfurt) — all existing infra is here
- Account: 553637109631
- Domain: javazone.no (java.no DNS not in AWS yet)
- Create NEW state bucket (`javabin-terraform-state-*`) — existing `javazone-terraform-state` is unrelated
- Bedrock: all models available, no per-model access request needed
- Coexistence: new platform infra runs parallel to existing CDK/EB/ALB resources

## Deliverables

### 1. `scripts/bootstrap.sh`
One-time script creating:
- `javabin-terraform-state-{account_id}` S3 bucket (versioning, public block)
- `javabin-terraform-infra-lock` DynamoDB table (PAY_PER_REQUEST)
- `javabin-terraform-app-locks` DynamoDB table (PAY_PER_REQUEST)
- `javabin-ci-plan-artifacts-{account_id}` S3 bucket

### 2. `terraform/state/`
Terraform module managing the bootstrapped resources:
- `main.tf` — S3 buckets (KMS encryption, versioning, public block, lifecycle rules),
  DynamoDB tables, plan artifact bucket (24h expiry lifecycle)
- `variables.tf` — region, account_id, project (default: "javabin")
- `outputs.tf` — bucket names, table names
- `providers.tf` — AWS provider with default tags
- `backend.tf` — starts as `backend "local"`, with comments explaining migration to S3

### 3. `docs/bootstrap-runbook.md`
Step-by-step instructions for Alexander:
1. Run bootstrap.sh
2. terraform init (local backend)
3. terraform import (each resource)
4. Switch backend.tf to S3
5. terraform init -migrate-state
6. Delete local .tfstate
7. Commit

## State Layout in S3
```
javabin-terraform-state-{account-id}/
  state/terraform.tfstate              (this module)
  infra/terraform.tfstate              (platform module — Phase 2)
  org/terraform.tfstate                (org module)
  apps/{repo-name}/terraform.tfstate   (one per registered app)
```

## Rules
- Use variables for account ID, region — never hardcode
- The bootstrap script is the ONLY manual AWS CLI step in the entire platform
- Everything else is Terraform from this point forward
- Follow naming: `javabin-terraform-*` for state infra, `javabin-ci-*` for CI artifacts
