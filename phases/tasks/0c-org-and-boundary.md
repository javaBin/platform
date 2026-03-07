# Task 0c — Organizations + Permission Boundary

**Status:** Not started
**Phase:** 0 (Foundation)
**Depends on:** 0b (state backend must exist)
**Blocks:** 2c (IAM/OIDC roles need the boundary)

## Objective

Create the AWS Organizations setup and the permission boundary policy. The org module is
human-applied (no CI). The boundary goes into the platform module but is foundational.

## Context

Read `phases/phase-0-foundation.md` for full context.
Read `PLAN.md` sections on Organizations and Permission Boundary.

## Deliverables

### 1. `terraform/org/`
Human-applied module (no CI pipeline). Contains:
- `main.tf` — AWS Organization, SCP for requiring permission boundary
- `variables.tf` — account_id, boundary policy ARN
- `outputs.tf` — org ID, SCP ID
- `backend.tf` — S3 backend, key = `org/terraform.tfstate`
- `providers.tf` — AWS provider with default tags

**SCP details:**
- Deny `iam:CreateRole` and `iam:PutRolePermissionsBoundary` unless the boundary
  `javabin-developer-boundary` is attached
- Exempt platform CI role and admin role via condition on principal
- **Do NOT apply until the boundary policy exists and CI roles reference it**

### 2. `terraform/platform/iam/boundary.tf`
The `javabin-developer-boundary` IAM policy. Self-replicating — any role carrying this
boundary can only create roles that also carry it.

**Hard denies:**
- IAM roles/users/access keys without this boundary
- Modifying/deleting this boundary policy
- IAM Identity Center, Organizations, SCPs
- GuardDuty, Config, Security Hub, CloudTrail
- VPC, subnets, IGW, NAT gateway
- Platform ECS cluster, ALB, ACM certs
- S3 `javabin-terraform-*` and `javabin-ci-*` buckets
- DynamoDB `javabin-terraform-*` tables

**Allow everything else.**

### 3. `docs/org-runbook.md`
Instructions for applying the org module manually (Identity Center credentials).
This module never gets a CI pipeline — document why and how to apply.

## Rules
- The org module has NO CI pipeline — this is by design
- SCP must NOT be applied before the boundary policy exists
- Use variables for all ARNs and account IDs
- The boundary is the cornerstone of the IAM security model — get it right
- Reference the ai-platform-infra `terraform/iam/main.tf` for the boundary pattern
