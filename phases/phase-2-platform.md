# Phase 2 — Platform Infrastructure

## Agent Brief

You are building the core AWS platform for Javabin. This is the largest phase — it creates
the VPC, ALB, CI IAM roles, ECS cluster, monitoring stack, and Cognito identity pools.
Everything is adapted from the ai-platform-infra reference implementation.

**Prerequisites:** Phase 0 (state backend, permission boundary) and Phase 1 (Identity Center)
are complete. The CI handoff happens midway through this phase — early resources are applied
manually, then CI takes over.

**Repo:** `javaBin/platform`
**AWS Region:** eu-west-1 (or whatever discovery confirmed)
**Domain:** java.no
**GitHub Org:** javaBin

---

## Context: Reference Implementation

The ai-platform-infra repo (copied into this repo under `terraform/`) is the known-good
baseline. Study these modules and adapt them:

| Reference Module | Purpose | Javabin Module |
|---|---|---|
| `terraform/networking/` | VPC, subnets, NAT, SGs | `terraform/platform/networking/` |
| `terraform/ingress/` | ALB, ACM, Route53 | `terraform/platform/ingress/` |
| `terraform/iam/` | OIDC, CI roles, boundary | `terraform/platform/iam/` |
| `terraform/compute/` | ECS cluster | `terraform/platform/compute/` |
| `terraform/monitoring/` | SNS, EventBridge, Config, GuardDuty | `terraform/platform/monitoring/` |
| `terraform/lambdas/` | Lambda functions, OIDC discovery | `terraform/platform/lambdas/` |
| (new) | Cognito user pools | `terraform/platform/identity/` |

Key naming changes: `ai-platform-` → `javabin-`, `cxense.com` → `java.no`,
`eu-central-1` → `eu-west-1`, `gitlab-runner-*` → `javabin-ci-*`.

---

## Architecture Overview

```
GitHub Actions ──► GitHub OIDC ──► AWS IAM Roles ──► Terraform apply

┌──────────── Platform (ECS Fargate, Javabin) ─────────────┐
│                                                            │
│  ALB (*.java.no) ──► ECS Services (per-app)               │
│  Cognito (internal) ──► Heroes (@java.no)                 │
│  Cognito (external) ──► Attendees (self-signup)           │
│  Lambdas: slack-alert, cost-report, daily-cost-check      │
│  Monitoring: GuardDuty, Security Hub, Config, EventBridge │
│                                                            │
└────────────────────────────────────────────────────────────┘
```

---

## Task 1: Networking (`terraform/platform/networking/`)

Adapt `terraform/networking/` from the reference. Check discovery results first — if a
VPC already exists, import it instead of creating.

**Resources:**
- VPC: `10.0.0.0/16` (or import existing)
- Public subnets: `10.0.1.0/24`, `10.0.2.0/24` (eu-west-1a, eu-west-1b)
- Private subnets: `10.0.10.0/24`, `10.0.11.0/24`
- Internet Gateway
- Single NAT Gateway (cost saving, ~$38/mo, acceptable for internal tools)
- Route tables: public → IGW, private → NAT
- Security groups:
  - ALB SG: Allow HTTPS (443) + HTTP (80) from `0.0.0.0/0`
  - ECS Tasks SG: Allow container port from ALB SG only

**Outputs:** `vpc_id`, `public_subnet_ids`, `private_subnet_ids`, `alb_sg_id`, `ecs_tasks_sg_id`

---

## Task 2: Ingress (`terraform/platform/ingress/`)

Adapt `terraform/ingress/`. Route53 zone for java.no should already exist — import it.

**Resources:**
- Route53: Import existing `java.no` hosted zone (DO NOT recreate)
- ACM wildcard cert: `*.java.no`, DNS validation via Route53
- ALB: `javabin-platform-alb`, internet-facing, public subnets
- HTTPS listener (443): TLS 1.2+, default 404 response
- HTTP listener (80): Redirect to HTTPS (301)

**Outputs:** `alb_arn`, `alb_dns_name`, `alb_zone_id`, `https_listener_arn`

---

## Task 3: IAM / OIDC (`terraform/platform/iam/`)

This is the most critical module. Adapt `terraform/iam/` — replace GitLab OIDC with
GitHub OIDC, replace custom OIDC provider with `job_workflow_ref` conditions.

### GitHub OIDC Provider
```hcl
resource "aws_iam_openid_connect_provider" "github" {
  url             = "https://token.actions.githubusercontent.com"
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = ["ffffffffffffffffffffffffffffffffffffffff"]
}
```

### Four CI Roles

**1. `javabin-ci-infra`** — platform repo only
- Trust: GitHub OIDC, `repo:javaBin/platform:ref:refs/heads/main`
- Permissions: Allow `*:*` inline (with permission boundary)
- Additional explicit deny block for things CI should never do:
  ```json
  {
    "Effect": "Deny",
    "Action": [
      "organizations:*", "account:*",
      "iam:CreateUser", "iam:CreateAccessKey", "iam:CreateLoginProfile",
      "identitystore:CreateUser",
      "sso:CreateInstance", "sso:DeleteInstance",
      "cloudtrail:DeleteTrail", "cloudtrail:StopLogging", "cloudtrail:UpdateTrail",
      "guardduty:DeleteDetector", "securityhub:DisableSecurityHub",
      "config:DeleteConfigurationRecorder", "config:StopConfigurationRecorder",
      "s3:DeleteBucket"
    ],
    "Resource": "*"
  }
  ```
- THIS IS THE CI HANDOFF ROLE. Once this role exists and GitHub Actions can assume it,
  all subsequent platform resources go through the pipeline.

**2. `javabin-ci-app-{repo-name}`** — one per registered app repo
- Trust: GitHub OIDC with TWO conditions:
  - `sub` matches `repo:javaBin/{repo-name}:*`
  - `job_workflow_ref` matches `javaBin/platform/.github/workflows/tf-plan.yml@refs/heads/main`
- Permissions: Allow `*:*` with tag-based condition:
  ```json
  {
    "Condition": {
      "StringEqualsIfExists": {
        "aws:ResourceTag/project": "${aws:PrincipalTag/project}",
        "aws:RequestTag/project": "${aws:PrincipalTag/project}"
      }
    }
  }
  ```
- Permission boundary: `javabin-developer-boundary`
- STS session tags: `project` = `github.repository`, `GitHubActor`, `GitHubSHA`, `GitHubRunID`

**3. `javabin-ci-deploy-{repo-name}`** — one per registered app repo
- Trust: GitHub OIDC with `job_workflow_ref` = `javaBin/platform/.github/workflows/tf-apply.yml@refs/heads/main`
- Permissions: ECR push for that repo's ECR, ECS update service, CloudWatch logs
- This is the critical lock — app repo cannot write a workflow that assumes this role

**4. `javabin-ci-override-approver`** — for override workflow only
- Trust: GitHub OIDC with TWO conditions:
  - `job_workflow_ref` = `javaBin/platform/.github/workflows/approve-override.yml@refs/heads/main`
  - `actor` in `var.override_approvers` list
- Permissions: `ssm:PutParameter` on `/javabin/overrides/*` only

### Permission Boundary
Already created in Phase 0 (`javabin-developer-boundary`). Reference it here.

### ECS Execution Role
```hcl
resource "aws_iam_role" "ecs_execution" {
  name = "javabin-ecs-execution"
  assume_role_policy = # ECS tasks service principal
  # Permissions: ECR pull, CloudWatch Logs, Secrets Manager read
}
```

### Variables
```hcl
variable "registered_app_repos" {
  description = "App repos registered via javaBin/registry"
  type        = list(string)
  default     = []  # Populated by registry provisioning
}

variable "override_approvers" {
  description = "GitHub usernames permitted to approve risk gate overrides"
  type        = list(string)
  default     = ["alexander-amiri"]
}
```

**Outputs:** `ecs_execution_role_arn`, role ARNs for each app

---

## Task 4: Compute (`terraform/platform/compute/`)

**Resources:**
- ECS Cluster: `javabin-platform`, Fargate, Container Insights enabled
- ECR repos for CI images (JVM, TypeScript, platform)

**Outputs:** `ecs_cluster_name`, `ecs_cluster_arn`

---

## Task 5: Monitoring (`terraform/platform/monitoring/`)

Adapt `terraform/monitoring/`. Add Security Hub (not in reference).

**Resources:**

### SNS Topics
- `javabin-alerts` — cost anomalies, budgets
- `javabin-security` — security events

### EventBridge Rules (→ javabin-security SNS)
1. IAM Changes (CreateRole, DeleteRole, PutRolePolicy, AttachRolePolicy, etc.)
2. Console Logins
3. Config Compliance (non-compliant resources)
4. Resource Creation (EC2, S3, RDS, ECS, ELB, Lambda, SQS, DynamoDB)
5. Resource Modification/Deletion

### AWS Config
- Recorder on all resources
- S3 bucket for Config logs
- Config Rule: required tags (`project`, `team`, `managed-by`)

### Cost Anomaly Detection
- Monitor type: DIMENSIONAL (by SERVICE)
- Threshold: $10 absolute spike → javabin-alerts SNS
- Frequency: IMMEDIATE

### GuardDuty
- Enable threat detection
- ECS Runtime Monitoring (Fargate agent auto-managed)
- Findings → Security Hub (not direct to SNS)

### Security Hub (NEW — not in reference)
- Enable Security Hub
- Connect GuardDuty findings → Security Hub
- Connect Config findings → Security Hub
- Security Hub → SNS → slack-alert Lambda
- This normalizes findings before alerting, reduces noise

**Outputs:** `alerts_topic_arn`, `security_topic_arn`

---

## Task 6: Lambda Functions (`terraform/platform/lambdas/`)

Adapt all Lambda functions from the reference. Key changes:
- Replace GitLab source enrichment with GitHub OIDC session tag parsing
- Replace `ai-platform-*` naming with `javabin-*`
- Replace SSM paths `/ai-platform/*` with `/javabin/*`
- Change auto-tagger to compliance reporter (report untagged, don't auto-fix)
- Add Security Hub as a finding source for slack-alert
- Extract shared pricing utility (used by both slack-alert and plan review)

### Lambda 1: `javabin-slack-alert`
- Trigger: SNS (javabin-alerts, javabin-security)
- Features: LLM risk analysis via Bedrock, Pricing API, dedup, console URLs
- GitHub attribution: parse OIDC sub claim and session tags
- SSM: `/javabin/slack/infra-alerts-webhook`, `/javabin/slack/cost-alerts-webhook`

### Lambda 2: `javabin-cost-report`
- Trigger: EventBridge (Monday 08:00 UTC)
- Features: Weekly breakdown (WoW, MTD, projected), per-project tag breakdown
- LLM narrative + spike root cause
- Costs in NOK (not USD) — use exchange rate or note conversion

### Lambda 3: `javabin-daily-cost-check`
- Trigger: EventBridge (daily 08:00 UTC)
- Features: Yesterday vs 7-day average, silent if no spikes
- No Bedrock exclusion needed (Javabin doesn't use Bedrock for AI workloads)

### Lambda 4: `javabin-auto-tagger` (simplified → compliance reporter)
- Trigger: EventBridge (Create events)
- Change from reference: Do NOT auto-tag. Instead, post to Slack when untagged
  resources are detected. Tags are injected at creation via CI — the reporter catches
  anything that slips through.

### Lambda 5: `javabin-team-provisioner` (NEW — not in reference)
- Trigger: GitHub repository dispatch from `javaBin/registry` on team YAML merges
- Syncs team state across four systems:
  1. Google Workspace — create/update group, sync membership
  2. GitHub — create/update team, set repo access
  3. Cognito — create/update group in internal pool
  4. IAM Identity Center — map group to permission set with team tag
  5. AWS Budget — create/update team-level budget
- API clients needed:
  - Google Admin SDK (service account with domain-wide delegation)
  - GitHub API (GitHub App with org permissions)
  - AWS SDK (native, via Lambda execution role)
- SSM: `/javabin/platform/google-admin-sa`, `/javabin/platform/github-app-key`

### Lambda 6: `javabin-override-cleanup` (NEW)
- Trigger: EventBridge (hourly)
- Deletes SSM parameters under `/javabin/overrides/*` older than 24 hours
- Prevents pre-approved overrides from persisting

### Shared pricing utility
Extract Pricing API logic from slack-alert into a shared Lambda layer or utility module.
Both slack-alert and the plan review Lambda use it.

---

## Task 7: Cognito User Pools (`terraform/platform/identity/`)

Add to the identity module created in Phase 1.

### Internal Pool (`javabin-internal`)
- For heroes and board members
- Google federation: `hd=java.no` restriction (only @java.no accounts)
- Cognito domain: `auth-internal.auth.eu-west-1.amazoncognito.com`
  (later: custom domain `auth.java.no`)
- Groups mirroring Google Workspace groups: heroes, board, pkom, infra
- MFA optional at pool level (Google 2FA is the enforcement layer)

### External Pool (`javabin-external`)
- For conference attendees, speakers
- Self-service signup enabled
- Email verification required
- Social providers: Google (public, not hd-restricted), GitHub
- Cognito domain: `auth.auth.eu-west-1.amazoncognito.com`
  (later: custom domain `login.javazone.no`)

### Cognito App Client Module (`modules/cognito-app-client/`)
Reusable module for app repos:
```hcl
module "app_auth" {
  source   = "github.com/javaBin/platform//modules/cognito-app-client"
  app_name = "cake-redux"
  pool     = "internal"  # or "external" or "both"
  # Creates: app client, stores CLIENT_ID + CLIENT_SECRET in SSM
  # Callback URLs: {app-name}.java.no/callback
}
```

When `app.yaml` contains `auth: internal`, the golden path generates this module call.

---

## Task 8: CI Handoff and GitHub Actions Platform Workflow

**This is the critical milestone.** Once `javabin-ci-infra` role exists and can be assumed
by GitHub Actions, create the platform CI workflow:

`.github/workflows/platform-ci.yml`:
- Triggers on push to main (paths: `terraform/platform/**`)
- Jobs: plan → review → apply
- Uses OIDC to assume `javabin-ci-infra`
- Plan artifacts uploaded to S3 with SHA256
- LLM review via Bedrock (same pattern as reference `.gitlab-ci.yml`)
- Auto-apply on LOW/MEDIUM risk, block on HIGH

From this point, no human runs `terraform apply` for platform resources.

---

## Constraints

- **Import existing resources** (VPC, Route53 zone) — do not recreate.
- **Variables, not hardcodes** — account ID, region, domain all via variables.
- **Default tags via provider** — project, team, managed-by, commit, created-by (from CI env).
- **Secrets via SSM** — webhook URLs, API keys. Never in env vars or TF variables.
- **Permission boundary on all CI roles** — even the infra role (plus explicit deny block).
- **Single NAT Gateway** — cost saving, acceptable for internal tools.
- **No Bedrock exclusion in daily cost check** — Javabin doesn't use Bedrock for AI workloads.

## Deliverables

1. `terraform/platform/` — complete platform module with all sub-modules:
   - `main.tf`, `providers.tf`, `variables.tf`, `outputs.tf`, `backend.tf`
   - `networking/`, `ingress/`, `iam/`, `compute/`, `monitoring/`, `lambdas/`, `identity/`
2. `terraform/lambda-src/` — adapted Lambda source code for all 6 functions
3. `modules/cognito-app-client/` — reusable Cognito module
4. `.github/workflows/platform-ci.yml` — platform CI workflow
5. Updated CLAUDE.md if any structural decisions changed

## What Comes Next (DO NOT implement)

Phase 3 builds the golden path CI workflows, registry, team provisioner, and CLI.
Phase 4 onboards existing apps (cake-redux, moresleep, etc.).
