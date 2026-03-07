# Phase 0 — Foundation

## Agent Brief

You are setting up the foundational AWS infrastructure for **Javabin** (java.no), the Norwegian
Java User Group. This is the very first phase — nothing exists yet in Terraform. Your goal is
to create the bootstrapping layer that all subsequent phases build on.

**Repo:** `javaBin/platform` (local for now, will be pushed to GitHub later)
**Working directory:** This repo's `terraform/` directory
**AWS Region:** eu-west-1 (Ireland) — confirm via discovery first, adapt if existing infra differs
**Domain:** java.no
**Naming prefix:** `javabin-` (not `ai-platform-`)

---

## Context: What Javabin Is

Javabin is a non-profit volunteer org running Java meetups across Norway and JavaZone conference.
~70 active "hero" volunteers. GitHub org: `javaBin`. Domain: java.no. Google Workspace with
@java.no accounts. Existing AWS account with some ad-hoc infra (Coolify EC2, Vaultwarden,
possibly Elastic Beanstalk environments for cake-redux and moresleep).

## Context: Reference Implementation

This platform is adapted from Alexander's ai-platform-infra repo at his day job (Cxense/Piano).
That used GitLab CI, eu-central-1, `ai-platform-*` naming, and `cxense.com` domain. We are
adapting it for GitHub Actions, eu-west-1, `javabin-*` naming, and `java.no` domain.

Key files from the reference to study (in the `terraform/` directory copied into this repo):
- `terraform/backend.tf` — S3 + DynamoDB state backend pattern
- `terraform/providers.tf` — Provider config with default_tags
- `terraform/variables.tf` — Root variables
- `terraform/iam/` — Permission boundary and CI roles pattern

---

## Task 1: AWS Discovery (MUST DO FIRST)

Before writing any Terraform, run discovery against the existing AWS account. Use `aws` CLI.
Document everything you find. Check for and document:

- Existing VPC(s) and CIDR ranges
- Existing IAM users, roles, and policies (especially admin users)
- Existing EC2 instances (find the Coolify instance and Vaultwarden)
- Existing S3 buckets
- Existing DynamoDB tables
- Existing Route53 hosted zones (java.no must exist)
- Existing ACM certificates
- Existing ECS clusters or services
- Any existing Elastic Beanstalk environments (cake-redux and moresleep are known)
- Any existing CloudFormation stacks (from CDK or EB)
- Current region the account operates in
- Whether AWS Organizations is set up
- Whether IAM Identity Center is configured
- Whether GuardDuty is enabled
- Whether Security Hub is enabled
- Current billing/cost data for baseline spend

**Do not create anything that duplicates existing resources.**
**Do not delete or modify any existing resources.**

Write discovery results to `phases/discovery-results.md`.

---

## Task 2: Bootstrap Script

Create `scripts/bootstrap.sh` — a one-time script that creates the state infrastructure
before Terraform can manage it. This is the ONLY manual CLI step in the entire platform.

```bash
#!/bin/bash
set -euo pipefail
# bootstrap.sh — run ONCE to create state infrastructure
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="eu-west-1"  # Adapt if discovery shows a different region

# State bucket
aws s3api create-bucket \
  --bucket javabin-terraform-state-${ACCOUNT_ID} \
  --region ${REGION} \
  --create-bucket-configuration LocationConstraint=${REGION}

aws s3api put-bucket-versioning \
  --bucket javabin-terraform-state-${ACCOUNT_ID} \
  --versioning-configuration Status=Enabled

aws s3api put-public-access-block \
  --bucket javabin-terraform-state-${ACCOUNT_ID} \
  --public-access-block-configuration \
  "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

# Infra lock table (for platform repo state)
aws dynamodb create-table \
  --table-name javabin-terraform-infra-lock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region ${REGION}

# App lock table (for app repo states)
aws dynamodb create-table \
  --table-name javabin-terraform-app-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region ${REGION}

# Plan artifact bucket (for CI plan integrity)
aws s3api create-bucket \
  --bucket javabin-ci-plan-artifacts-${ACCOUNT_ID} \
  --region ${REGION} \
  --create-bucket-configuration LocationConstraint=${REGION}

echo "Bootstrap complete. Run terraform init next."
```

After bootstrap runs, immediately import these resources into Terraform.

---

## Task 3: State Module (`terraform/state/`)

Create `terraform/state/` with Terraform for the bootstrapped resources:

**Files to create:**
- `terraform/state/main.tf` — S3 bucket (versioning, encryption, public block, lifecycle),
  DynamoDB tables (infra-lock, app-locks), plan artifact bucket (24h lifecycle rule)
- `terraform/state/variables.tf` — region, account_id, project
- `terraform/state/outputs.tf` — bucket names, table names
- `terraform/state/backend.tf` — starts as `backend "local"`, then migrates to S3
- `terraform/state/providers.tf` — AWS provider with default tags

**Import sequence:**
1. Start with `backend "local"` in backend.tf
2. `terraform import aws_s3_bucket.state javabin-terraform-state-{account-id}`
3. `terraform import aws_dynamodb_table.infra_lock javabin-terraform-infra-lock`
4. `terraform import aws_dynamodb_table.app_locks javabin-terraform-app-locks`
5. `terraform import aws_s3_bucket.plan_artifacts javabin-ci-plan-artifacts-{account-id}`
6. Switch backend.tf to S3: `backend "s3" { bucket = "javabin-terraform-state-{id}", key = "state/terraform.tfstate", ... }`
7. `terraform init -migrate-state`
8. Delete local `.tfstate` file
9. Commit everything

**State layout in S3:**
```
javabin-terraform-state-{account-id}/
  state/terraform.tfstate              (this module)
  infra/terraform.tfstate              (platform module)
  org/terraform.tfstate                (org module)
  apps/{repo-name}/terraform.tfstate   (one per registered app)
```

---

## Task 4: AWS Organizations (`terraform/org/`)

Create `terraform/org/` — this module is human-applied only (no CI pipeline).

**Resources:**
- Enable AWS Organizations on the existing account (it becomes management account)
- Create one SCP: "require permission boundary on role creation"
  - Deny `iam:CreateRole` and `iam:PutRolePermissionsBoundary` unless
    `iam:PermissionsBoundary` equals `arn:aws:iam::{account}:policy/javabin-developer-boundary`
  - Exempt the platform CI role and break-glass admin from this SCP via condition
  - **Do NOT apply this SCP until the permission boundary policy exists (Phase 2)**

**Files to create:**
- `terraform/org/main.tf`
- `terraform/org/variables.tf`
- `terraform/org/outputs.tf`
- `terraform/org/backend.tf` — S3 backend, key = `org/terraform.tfstate`
- `terraform/org/providers.tf`

**Important:** This module has no CI pipeline. Apply is always manual via Identity Center
credentials. This is by design — a compromised CI pipeline cannot touch org structure or SCPs.

---

## Task 5: Permission Boundary Policy

Create the `javabin-developer-boundary` IAM policy. This goes in the platform module
(`terraform/platform/iam/`) but is foundational enough that it must exist before CI roles.

**Hard denies (cannot be overridden):**
- Creating IAM roles/users/access keys without attaching this same boundary (self-replicating)
- Modifying or deleting this boundary policy itself
- IAM Identity Center, Organizations, SCPs
- GuardDuty, Config, Security Hub, CloudTrail (protective services)
- VPC, subnets, IGW, NAT gateway (platform networking)
- The platform ECS cluster, ALB, ACM certs
- S3 buckets with prefix `javabin-terraform-*` and `javabin-ci-*`
- DynamoDB tables with prefix `javabin-terraform-*`

**Allow everything else.** A developer role can create ECS services, RDS instances,
Lambda functions, SQS queues, etc. — as long as those roles also carry the boundary.

---

## Constraints

- **Do not touch existing IAM admin users.** They continue working as before.
- **Do not create new IAM users.** All new access goes through Identity Center (Phase 1).
- **Commit before applying.** The sequence is always: write code → commit → apply.
- **State in S3 before anything else.** After bootstrap, the very first action is importing
  state resources into Terraform and migrating to S3 backend.
- Every resource must use variables for account ID, region, domain — never hardcode.
- All resources tagged via provider default_tags.

## Deliverables

1. `phases/discovery-results.md` — complete AWS discovery report
2. `scripts/bootstrap.sh` — one-time state bootstrap script
3. `terraform/state/` — state management module (S3 + DynamoDB)
4. `terraform/org/` — Organizations + SCP module (human-applied)
5. `terraform/platform/iam/boundary.tf` — permission boundary policy
6. Clear instructions for the import/migrate sequence

## What Comes Next (DO NOT implement)

Phase 1 handles Identity Center + Google Workspace SAML integration.
Phase 2 handles VPC, ALB, OIDC, ECS, monitoring, Cognito, and the full CI pipeline.
