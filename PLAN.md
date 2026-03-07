# Javabin Platform Infrastructure — Agent Brief

## Who you are working for

**Javabin** is the Norwegian Java User Group (java.no), one of Norway's largest developer communities.
It is a non-profit volunteer organisation with branches in Oslo, Bergen, Trondheim, Stavanger,
Tromsø, Vestfold, Sogn, and Sørlandet. It has operated for over 24 years and won the Duke's
Choice Award in 2019.

Javabin hosts:
- **Monthly meetups** across all branches
- **JavaZone** — one of the world's largest community-driven Java conferences (~3,600 attendees,
  ~200 speakers, ~50 partners, held every September in Oslo Spektrum)
- Student and kids events, volunteer coordination

**Legal and financial structure:**
- **Javabin** (non-profit) — the parent organisation. Board-governed. ~4,000 members (JavaZone
  ticket = Javabin membership). ~60–70 active "hero" volunteers with special access and a free
  conference ticket.
- **Javazone Økonomi AS** (for-profit subsidiary) — runs the commercial side of JavaZone.
  ~30M NOK flows through it annually. ~2M NOK transferred back to Javabin per year to fund
  meetups, volunteer food, and operations.
- Javabin has strong economic reserves — roughly 10 years of runway without any JavaZone income.

**Domain:** java.no
**Google Workspace non-profit:** Active. Used for @java.no emails (faktura@java.no etc) and
Google Groups. Non-profit status = free Business Starter licenses.
**GitHub non-profit:** Active. Repos are open source at github.com/javaBin.
**AWS account:** Exists. No formal CICD. Users added by private email. No 2FA enforced.
No README. Has some infra including a Coolify instance on a limited EC2 with Vaultwarden.
**Atlassian / Confluence:** Exists. Messy documentation accumulated over 20 years.

**Current internal systems (discovered from GitHub):**
- `cake-redux` — Java/Kotlin/TypeScript, Maven, Dockerfile, has CDK folder. Program committee
  tool for reviewing and scheduling CFP talks. Currently deployed via **Elastic Beanstalk**.
  Has `.github/workflows` already. Has an old `.travis.yml` that should be removed.
- `moresleep` — Kotlin, Maven, Dockerfile, Postgres via docker-compose. Session/talk database
  replacing the old sleepingPillCore. Currently deployed via **Elastic Beanstalk**.
- `cupcake` — Kotlin. Simpler read-only version of cake for regional branch access.
- `annual-meeting-membership` — Kotlin. Lookup service for checking Javabin membership.
- `submittheforce` — current CFP submission system for speakers.
- `2025.javazone.no-native` — TypeScript. New React Native app replacing year-specific sites.
- `java.no` — TypeScript. The main Javabin website.
- ~110 total repos, many are legacy archived year-specific conference sites (javazone-2020 etc).

Both core services (cake-redux, moresleep) are on Elastic Beanstalk — not ECS.
Migration to ECS is part of Phase 4. The golden path CI must support EB during transition.

---

## The person you are working with

**Alexander Amiri** — board member of Javabin. Has `@java.no` Google account. Has AWS admin
access. Has GitHub org admin. Has Google Workspace admin. He built the `ai-platform-infra`
Terraform repository (described in full below) at his day job (Cxense/Piano). That setup is
the reference implementation for what he wants to build for Javabin. You should treat that
entire setup as the known-good baseline and adapt it, not replace it.

---

## Reference implementation: ai-platform-infra

The following is the complete reference architecture Alexander built at his day job. Read it
carefully — it defines the patterns, module structure, IAM model, CI pipeline, Lambda monitoring
stack, cost tooling, and tagging strategy you will adapt for Javabin.

Key patterns to carry over verbatim (with naming changes):
- OIDC federation for CI (no stored credentials)
- Permission boundary with self-replicating condition
- Four CI roles (infra, app-terraform, deploy, ci-auth equivalent)
- Lambda monitoring stack (slack-alert with LLM gating, cost-report, daily-cost-check)
- S3 + DynamoDB state backend
- Auto-tagger (simplified — see notes below)
- LLM plan review in CI (risk gates apply)
- STS session tags for CloudTrail attribution
- EventBridge → SNS → Lambda alert routing
- GuardDuty, Config, Security Hub
- Default provider tags from CI environment variables

### Changes from the reference implementation

| Reference (Cxense) | Javabin adaptation |
|---|---|
| GitLab CI / GitLab OIDC | GitHub Actions / GitHub OIDC |
| `gitlab-runner-*` roles | `javabin-ci-*` roles |
| `gl__{project}__{sha}__{pipeline}` session name | Not needed — OIDC sub claim is the session name automatically |
| Single repo/account | Multi-repo golden path |
| ci-authorizer Lambda (custom OIDC) | GitHub environment protection (simpler, sufficient) |
| `piano/cxense/dexa/` namespace | `javaBin/` GitHub org |
| `*.ai.cxense.com` | `*.java.no` |
| eu-central-1 | eu-west-1 (Ireland, check existing AWS region first) |
| Bedrock eu.anthropic.claude-haiku-4-5-20251001-v1:0 | Same model, confirm availability in chosen region |
| ECS cluster: ai-platform | ECS cluster: javabin-platform |
| Slack channels: ai-platform-* | Slack channels: javabin-infra-* |

---

## Your first task: discovery

**Before writing any Terraform, run discovery against the existing AWS account.**

Check for and document:
- Existing VPC(s) and CIDR ranges
- Existing IAM users, roles, and policies (especially admin users)
- Existing EC2 instances (find the Coolify instance and Vaultwarden)
- Existing S3 buckets
- Existing DynamoDB tables
- Existing Route53 hosted zones (java.no must exist)
- Existing ACM certificates
- Existing ECS clusters or services
- Any existing Elastic Beanstalk environments (cake-redux and moresleep are known to use EB)
- Any existing CloudFormation stacks (may be from CDK deployments or EB)
- Current region the account operates in
- Whether AWS Organizations is set up
- Whether IAM Identity Center is already configured
- Whether GuardDuty is already enabled
- Whether Security Hub is already enabled
- Current billing/cost data to understand baseline spend

Do not create anything that duplicates existing resources. Import existing resources into
Terraform state where appropriate.

---

## What to build — phased

## Terraform-first philosophy

Everything lives in Terraform and in the `javaBin/platform` repo from the very first
resource created. There is no "set up manually first, migrate to Terraform later."
The only exception is the two-resource bootstrapping problem described below.

**Everything in the repo from day one — including pre-CI applies:**
Before CI is wired up, `terraform apply` is run manually from Alexander's local machine
using Identity Center credentials. This is fine and expected. What matters is that the
Terraform code exists in the repo and the state is in S3 before any subsequent resource
is touched. The commit history covers the pre-CI period — every change is visible in git
and attributed in CloudTrail via the Identity Center session.

Never apply anything without first committing the Terraform that describes it. The
sequence is always: write code → commit → apply (manually until CI exists, via pipeline
after). If something was created manually and not yet in Terraform, the next step is
`terraform import` before anything else.

**The CI pipeline takes over as soon as it can. Do not delay this.** The OIDC provider,
CI role, and reusable workflows should be wired up and tested before Phase 2 proceeds
further. From that point forward, no human runs `terraform apply` for platform resources
— PRs go through the pipeline. Manual applies are reserved for `terraform/org/` (SCPs,
Organizations) which is intentionally CI-free.

**.gitignore must include from commit one:**
```
*.tfstate
*.tfstate.backup
.terraform/
**/.terraform/
*.tfplan
```

---

### Phase 0 — Foundation (do this first, non-destructive, existing access unaffected)

**AWS Organizations**
Set up an AWS Organization on the existing account (it becomes the management/root account).
AWS Organizations is free regardless of account count — no charge for the organisation itself,
SCPs, or consolidated billing features. You only pay for resources inside accounts.

Create one SCP initially — "require permission boundary on role creation":
```json
{
  "Condition": {
    "StringNotEquals": {
      "iam:PermissionsBoundary": "arn:aws:iam::{account}:policy/javabin-developer-boundary"
    }
  },
  "Effect": "Deny",
  "Action": ["iam:CreateRole", "iam:PutRolePermissionsBoundary"],
  "Resource": "*",
  "Sid": "RequirePermissionBoundary"
}
```

Exempt the platform CI role and the break-glass admin role from this SCP using a
condition on the principal. Do not apply this SCP until the permission boundary policy
and platform CI role exist.

**S3 + DynamoDB state backend — bootstrapping**

Bootstrap script creates the state bucket and lock tables before Terraform can manage them.
Run once, then immediately import into Terraform.

Step 1 — Run `platform/scripts/bootstrap.sh`:
```bash
#!/bin/bash
# bootstrap.sh — run ONCE to create state infrastructure
# Everything else is Terraform from this point forward.
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="eu-west-1"

aws s3api create-bucket \
  --bucket javabin-terraform-state-${ACCOUNT_ID} \
  --region ${REGION} \
  --create-bucket-configuration LocationConstraint=${REGION}

aws s3api put-bucket-versioning \
  --bucket javabin-terraform-state-${ACCOUNT_ID} \
  --versioning-configuration Status=Enabled

aws s3api put-public-access-block \
  --bucket javabin-terraform-state-${ACCOUNT_ID} \
  --public-access-block-configuration "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"

aws dynamodb create-table \
  --table-name javabin-terraform-infra-lock \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region ${REGION}

aws dynamodb create-table \
  --table-name javabin-terraform-app-locks \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region ${REGION}

aws s3api create-bucket \
  --bucket javabin-ci-plan-artifacts-${ACCOUNT_ID} \
  --region ${REGION} \
  --create-bucket-configuration LocationConstraint=${REGION}

echo "Bootstrap complete. Run terraform init next."
```

Step 2 — Write Terraform for these resources in `terraform/state/` with local backend initially:
```hcl
terraform {
  backend "local" {
    path = "terraform.tfstate"
  }
}
```

Step 3 — `terraform import` the bucket and both tables into local state.

Step 4 — Switch the backend to S3:
```hcl
terraform {
  backend "s3" {
    bucket         = "javabin-terraform-state-{account-id}"
    key            = "infra/terraform.tfstate"
    region         = "eu-west-1"
    dynamodb_table = "javabin-terraform-infra-lock"
    encrypt        = true
  }
}
```

Step 5 — `terraform init -migrate-state`, then delete the local `.tfstate` file.

Step 6 — Commit everything to `javaBin/platform`.

State layout in S3:
```
javabin-terraform-state-{account-id}/
  infra/terraform.tfstate           (platform repo — all platform resources)
  apps/{repo-name}/terraform.tfstate  (one prefix per registered app repo)
```

KMS encryption, versioning, and public access block are declared in the `terraform/state/`
module. The bootstrap script creates bare resources; Terraform owns the configuration.

**Plan artifact integrity (S3-based)**
```
Bucket: javabin-ci-plan-artifacts-{account-id}
  {github-run-id}/{repo-name}/plan.tfplan
  {github-run-id}/{repo-name}/plan.sha256
```

Plan job uploads plan + SHA256 hash. Apply job downloads and verifies hash before applying.
Plan job role can only write to this bucket. Apply job role can only read. Artifacts expire
after 24 hours (lifecycle rule). This prevents plan tampering between CI jobs.

**Permission boundary policy**
Create `javabin-developer-boundary` policy. Hard denies (cannot be overridden by any policy
attached to a role that carries this boundary):
- Creating IAM roles/users/access keys without attaching this same boundary (self-replicating)
- Modifying or deleting this boundary policy itself
- IAM Identity Center, Organizations, SCPs
- GuardDuty, Config, Security Hub, CloudTrail (protective services)
- VPC, subnets, IGW, NAT gateway (platform networking)
- The platform ECS cluster, ALB, ACM certs
- S3 buckets with prefix `javabin-terraform-*` and `javabin-ci-*`
- DynamoDB tables with prefix `javabin-terraform-*`

Allow everything else. This means a developer role can create ECS services, RDS instances,
Lambda functions, SQS queues, SNS topics, S3 app buckets, DynamoDB app tables, and their
own scoped IAM roles (as long as those roles also carry the boundary).

**Existing IAM admin users**
Leave all existing IAM admin users completely untouched during this phase. They continue
working exactly as before. Migrate them individually to Identity Center as they receive
their @java.no Google accounts. Do not create any new IAM admin users or break-glass accounts —
Google IdP is trusted as the identity source of truth.

---

### Phase 1 — Identity (Google Workspace → AWS)

**Goal:** Heroes get `@java.no` Google accounts. AWS access via Identity Center. Existing
IAM users continue working in parallel throughout.

**Google Workspace setup (Alexander does this manually, but document the steps):**

1. Bulk create hero accounts via CSV import in Google Admin Console or GAM CLI
   - Send welcome email with one-time password reset link (built into Workspace bulk create)
   - Heroes set their own passwords and enroll their own 2FA
2. Create Google Workspace Groups:
   - `heroes@java.no` — all active heroes (~70 people)
   - `board@java.no` — board members
   - `pkom@java.no` — program committee
   - `infra@java.no` — people with elevated AWS/infra access
   - `javazone-crew@java.no` — all JavaZone organizers
3. 2FA policy: set to "Optional" initially with a grace period. Enforce later when
   everyone has onboarded. This does not affect AWS IAM users at all.

**IAM Identity Center setup (Terraform):**

Enable IAM Identity Center in the AWS account. Configure Google Workspace as the external
SAML IdP. Google Workspace SAML setup steps:
- In Google Admin → Apps → Web and mobile apps → Add custom SAML app
- Service provider entity ID: `https://signin.aws.amazon.com/saml`
- Configure attribute mappings for groups

Create Permission Sets:
- `JavabinAdmin` — AdministratorAccess managed policy, for `infra@java.no` group
- `JavabinDeveloper` — read-only + ability to assume project roles via IAM, for `heroes@java.no`
- `JavabinReadOnly` — ViewOnlyAccess, for `board@java.no` and `pkom@java.no`

Assign groups to Permission Sets. Existing IAM users are unaffected — they continue to
work exactly as before. Migrate them one at a time as they get `@java.no` accounts.

---

### Phase 2 — Platform Infrastructure (adapt ai-platform-infra for Javabin)

**Adapt the reference Terraform repository as `javaBin/platform`.**

Module structure mirrors the reference implementation:
```
terraform/
  networking/    VPC, subnets, NAT, SGs
  ingress/       ALB, ACM, Route53
  iam/           OIDC, CI roles, permission boundary
  compute/       ECS cluster, ECR base config
  monitoring/    SNS, EventBridge, Config, GuardDuty, Security Hub
  lambdas/       slack-alert, cost-report, daily-cost-check, auto-tagger, team-provisioner
  identity/      IAM Identity Center, Cognito pools (new — not in reference)
  state/         S3 buckets, DynamoDB tables for state and plan artifacts
```

**Networking:**
Check if a VPC already exists. If yes, import it. If no, create:
- VPC: 10.0.0.0/16
- Public subnets: 10.0.1.0/24, 10.0.2.0/24 (eu-west-1a, eu-west-1b)
- Private subnets: 10.0.10.0/24, 10.0.11.0/24
- Single NAT gateway (cost saving, acceptable for internal tools)
- Internet gateway

**Ingress:**
- ACM wildcard cert: `*.java.no`
- ALB: javabin-platform-alb (internet-facing, public subnets)
- Route53: java.no hosted zone already exists — import it, do not recreate

**IAM / OIDC:**
Register GitHub Actions OIDC provider: `token.actions.githubusercontent.com`

Create four CI roles following the reference implementation model:

1. `javabin-ci-infra` — for platform repo only
   - Trust: GitHub OIDC, `repo:javaBin/platform:ref:refs/heads/main`
   - Permissions: Allow `*:*` inline, permission boundary exempted for this role
   - Used by: platform CI (terraform plan/apply)

2. `javabin-ci-app-{repo-name}` — one per registered app repo (created at registration time)
   - Trust: GitHub OIDC, `repo:javaBin/{repo-name}:*`
   - **Also requires `job_workflow_ref` to be `javaBin/platform/.github/workflows/tf-plan.yml@refs/heads/main`**
   - Permissions: Allow `*:*` inline with tag-based condition restricting to `project={repo-name}` resources
   - Permission boundary: `javabin-developer-boundary` attached
   - Used by: app repo CI (terraform plan — routed via javabin.yml → tf-plan.yml in platform)

3. `javabin-ci-deploy-{repo-name}` — one per registered app repo
   - Trust: GitHub OIDC, `repo:javaBin/{repo-name}:*`
   - **Also requires `job_workflow_ref` to be `javaBin/platform/.github/workflows/tf-apply.yml@refs/heads/main`**
   - This is the critical lock. The deploy role only grants credentials when the call is
     literally coming from `tf-apply.yml` in platform at main. The app repo owner
     cannot write their own workflow that assumes this role — that workflow would have a
     different `job_workflow_ref` and AWS rejects the STS call at the credential request
     level, before any workflow logic runs. There is no way around this from the app repo.
   - Permissions: ECR push for that repo's ECR repository, ECS update service, CloudWatch logs
   - Used by: app repo CI (docker build + ECS/EB deploy — routed via javabin.yml → tf-apply.yml)

4. `javabin-ci-override-approver` — for platform override workflow only
   - Trust: GitHub OIDC with two conditions that must both be true:
     ```json
     {
       "StringEquals": {
         "token.actions.githubusercontent.com:job_workflow_ref":
           "javaBin/platform/.github/workflows/approve-override.yml@refs/heads/main",
         "token.actions.githubusercontent.com:actor": [
           "alexander-amiri"
         ]
       }
     }
     ```
   - The `job_workflow_ref` locks it to the specific override workflow in platform.
     The `actor` list is the explicit allowlist of people whose trigger is honoured by AWS.
     Both conditions must pass. Anyone else triggering `approve-override.yml` gets a 403
     from STS — the workflow fires but gets no credentials and cannot write to SSM.
   - The actor list is a hardcoded Terraform variable (`var.override_approvers`). It is
     short (board members + infra leads) and changes rarely. Updating it is a Terraform
     PR to platform, which itself requires a platform-owner to merge.
   - Permissions: `ssm:PutParameter` on `/javabin/overrides/*` only. Nothing else.
   - Used by: the override approval workflow in platform exclusively

**Tag-based resource isolation:**

Developers (via CI or console) can only touch resources tagged with their own repo/identity.
This is enforced on the Allow side of the app CI role policy using `StringEqualsIfExists`,
not via Deny blocks with enumerated service actions:

```json
{
  "Sid": "AllowWithTagIsolation",
  "Effect": "Allow",
  "Action": "*",
  "Resource": "*",
  "Condition": {
    "StringEqualsIfExists": {
      "aws:ResourceTag/project": "${aws:PrincipalTag/project}",
      "aws:RequestTag/project": "${aws:PrincipalTag/project}"
    }
  }
}
```

`StringEqualsIfExists` evaluates each condition key independently: if the key exists
in the request context (the action supports tag-based authorization), the tag must match
the principal's project tag. If the key doesn't exist (the action doesn't support tags),
the condition passes. This means tag isolation works automatically for every current and
future AWS service that supports tags — no enumeration of service actions required.
Non-tag-aware actions fall through to the permission boundary, which blocks the
dangerous operations (IAM escalation, protective services, platform networking).

The `project` principal tag is the binding mechanism:
- CI sessions: set as STS session tag when assuming the OIDC role, value = `github.repository`
- Console sessions: set as an Identity Center attribute on the Permission Set,
  derived from their Google account or group membership

The platform infra role (`javabin-ci-infra`) does not carry this condition — its Allow
policy is unconditioned. It needs to touch everything.

Any IAM roles a developer creates also carry the boundary (self-replicating condition),
so resources created by those downstream roles are equally scoped.

**CloudTrail attribution via OIDC sub claim and session tags:**

With OIDC federation (`sts:AssumeRoleWithWebIdentity`), AWS automatically sets the
session name to the OIDC `sub` claim. For GitHub Actions, this is:
`repo:javaBin/{repo-name}:ref:refs/heads/main` — already under 64 characters for
every Javabin repo, and already contains the repo and ref. No custom session name
formatting is needed.

The detailed context is passed as STS session tags, which are also recorded in CloudTrail:
- `GitHubActor` = github.actor
- `GitHubSHA` = github.sha
- `GitHubRepo` = github.repository
- `GitHubRunID` = github.run_id

The slack-alert Lambda reads these session tags (not the session name) for attribution
when formatting alerts. It identifies CI sessions by the `repo:javaBin/` prefix in
the CloudTrail principal ID.

**ECS Platform Cluster:**
- Cluster: `javabin-platform`
- Container Insights: enabled
- This cluster hosts all internal tools via the app golden path

**Default tags (injected by CI, not by developers):**
```hcl
provider "aws" {
  default_tags {
    tags = {
      project    = var.project_name  # from github.event.repository.name
      repo       = var.repo          # from github.repository
      team       = var.team          # from app.yaml if present, else "unknown"
      managed-by = "terraform"
      commit     = var.commit_sha    # from github.sha
      created-by = var.actor         # from github.actor
    }
  }
}
```

These are injected as `TF_VAR_*` environment variables in the reusable CI workflow.
Developers never write tags. Config rule and auto-tagger catch anything that slips through.

**Monitoring (port from reference implementation):**

Keep all three Lambdas: `slack-alert`, `cost-report`, `daily-cost-check`.
Simplify `auto-tagger` — change from active tagger to compliance reporter only
(post to Slack when untagged resources found, don't attempt to backfill tags —
backfilling is fragile and tags are now injected at creation via CI).

Add Security Hub:
- Enable Security Hub in the account
- Connect GuardDuty findings → Security Hub (not direct to SNS)
- Connect Config findings → Security Hub
- Security Hub → SNS → slack-alert Lambda (replaces some direct EventBridge rules)
- This normalises findings before alerting, reduces noise

Slack channels to create:
- `#javabin-infra-alerts` — security events, IAM changes, resource changes
- `#javabin-cost-alerts` — cost anomalies, weekly reports, daily spikes

**Identity module (new, not in reference):**

*Internal Cognito User Pool (heroes and board):*
```hcl
resource "aws_cognito_user_pool" "internal" {
  name = "javabin-internal"
  # MFA optional at pool level (Google 2FA is the enforcement)
  # Password policy: minimal, since Google federation is primary
}

resource "aws_cognito_identity_provider" "google" {
  user_pool_id  = aws_cognito_user_pool.internal.id
  provider_name = "Google"
  provider_type = "Google"
  # hd=java.no restriction on Google OAuth to limit to @java.no accounts
}

resource "aws_cognito_user_pool_domain" "internal" {
  domain       = "auth-internal"
  user_pool_id = aws_cognito_user_pool.internal.id
  # Results in: auth-internal.auth.eu-west-1.amazoncognito.com
  # Later: auth.java.no with custom domain
}
```

Cognito groups mirroring Google Workspace groups:
- `heroes` — maps from Google group `heroes@java.no`
- `board` — maps from `board@java.no`
- `pkom` — maps from `pkom@java.no`
- `infra` — maps from `infra@java.no`

*External Cognito User Pool (conference attendees, speakers):*
```hcl
resource "aws_cognito_user_pool" "external" {
  name = "javabin-external"
  # Self-service signup enabled
  # Email verification required
  # Social providers: Google (public, not hd-restricted), GitHub
}

resource "aws_cognito_user_pool_domain" "external" {
  domain = "auth"
  # Later: login.javazone.no with custom domain
}
```

This pool serves: mine sider, speaker portal, conference calendar/favorites, recordings,
programme tracking. No Workspace account required. Users self-register with email or social.

*Reusable Terraform module for app auth clients:*
```hcl
module "app_auth" {
  source    = "github.com/javaBin/platform//modules/cognito-app-client"
  app_name  = "cake-redux"
  pool      = "internal"   # or "external" or "both"
  # Creates app client, stores CLIENT_ID + CLIENT_SECRET in SSM under
  # /javabin/apps/cake-redux/cognito-client-id and /cognito-client-secret
  # Callback URLs derived from app domain ({app-name}.java.no/callback)
}
```

When `app.yaml` contains `auth: internal`, the golden path generates this module call
automatically. The app gets Cognito credentials in SSM — no manual setup.

---

## Registry — separate repo

The registry lives in `javaBin/registry`, not in platform. These are different trust
levels and should not share a repo. It contains both app registrations and team
definitions — the two things that drive provisioning across GitHub, Google Workspace,
Cognito, and AWS.

```
registry/
  apps/
    cake-redux.yaml
    moresleep.yaml
    submittheforce.yaml
  teams/
    core.yaml
    nettside.yaml
    pkom.yaml
  README.md
```

**Access model:**
- **Private, visible to org members only.** Contains usernames and team membership
  — not appropriate for public viewing. Set repo visibility to Private. All javaBin org
  members can see it; the public cannot.
- Any org member can open a PR to register a new app or propose a team change (low barrier)
- Main branch protected: required reviewer from `@javaBin/platform-owners`
- Merging a registration PR triggers infrastructure provisioning in platform
  via GitHub repository dispatch event — the developer never touches platform directly

**Registration flow:**
1. Developer opens PR to registry adding their `apps/my-tool.yaml`
2. GitHub Actions workflow in registry detects new app PR, posts to Slack:
   ```
   🆕 New app registration request
   App:    my-tool
   Team:   pkom
   Repo:   github.com/javaBin/my-tool
   By:     @developer-username
   Review: github.com/javaBin/registry/pull/12
   ```
3. Infra owner reviews, merges PR
4. Merge triggers workflow → repository dispatch to platform
5. Platform-infra workflow creates: project IAM roles, AWS Budget, Cognito app client,
   SSM parameter namespace, Cost Explorer saved filter

**Slack alert implementation:**
A GitHub Actions workflow in registry, not a Lambda. Triggers on
`pull_request` opened where changed files match `apps/*.yaml` or `teams/*.yaml`. Posts to
`#javabin-infra-alerts` via the webhook URL in SSM. No new infrastructure needed —
the workflow uses the existing webhook. Alert includes direct PR link. Approve/deny
happens in GitHub (proper audit trail) not via Slack interactive buttons.

---

## Teams — definition, provisioning, and access scoping

Teams are defined in `registry/teams/` as YAML files. Merging a team YAML PR
automatically provisions or updates the team across GitHub, Google Workspace, Cognito,
and AWS — one PR, all four systems. App repos reference a team by name in their
`apps/{name}.yaml`, and that team name becomes the `team` tag on every AWS resource
the app creates. That tag is then the basis for access scoping in IAM.

**Default teams:**

| GitHub team | Google group | Repos | AWS team tag |
|---|---|---|---|
| `nettside` | `team-nettside@java.no` | java.no, 2025.javazone.no-native | `nettside` |
| `core` | `team-core@java.no` | cake-redux, moresleep, cupcake | `core` |
| `pkom` | `pkom@java.no` | submittheforce, annual-meeting-membership | `pkom` |
| `platform-owners` | `infra@java.no` | platform | `infra` |

New teams are added by opening a PR to `registry/teams/`. They do not need to match
any pre-existing list — the provisioner creates whatever is in the YAML.

**Team YAML schema:**

```yaml
# registry/teams/core.yaml
name: core
description: Core backend systems — cake-redux, moresleep, cupcake
google_group: team-core@java.no   # provisioner creates this group if it doesn't exist
members:
  - alexander.amiri               # username only — provisioner appends @java.no
  - hero.name
repos:
  - cake-redux
  - moresleep
  - cupcake
aws_budget_nok: 3000              # team-level budget across all team repos combined
```

Usernames are the part before `@java.no` — everyone in the registry has a `@java.no`
account so the domain is implicit. The provisioner appends `@java.no` when calling
the Google Admin SDK, and uses the username directly for GitHub. This keeps the YAML
clean and avoids repeating the domain across all team files.

**What merging a team YAML PR provisions:**

1. **Google Workspace** — creates `team-core@java.no` group if missing, syncs membership.
   Members in the YAML are added. Members removed from the YAML are removed from the group.
   Google group state after sync exactly matches the YAML.

2. **GitHub** — creates or updates `@javaBin/core` team, sets read access to listed repos
   for all members, write access for repo owners (derived from `apps/*.yaml` owner field).
   GitHub team membership synced from YAML.

3. **Cognito** — creates or updates the `core` group in the internal user pool.
   Membership synced from the Google group (not the YAML directly — Google is source of
   truth, Cognito reflects it via the provisioner reading current Google group membership).

4. **IAM Identity Center** — the `core` group is mapped to the `JavabinDeveloper`
   permission set with principal tag `team=core`. Heroes in this group get an AWS
   session tagged `team=core` when they log into the console. This tag is what IAM
   conditions check for resource access.

5. **AWS Budget** — creates or updates a team-level budget scoped to `team=core` tag
   across all repos in the team. Separate from per-app budgets. Alert to `#javabin-cost-alerts`.

**The team-provisioner Lambda:**

A Lambda function in platform, triggered by repository dispatch from registry on any
merge to `teams/*.yaml`. Runs with a dedicated IAM role. Needs three API clients:

- **Google Admin SDK** via a service account with domain-wide delegation. Alexander
  creates this once in Google Admin Console → Service Accounts, grants it
  `https://www.googleapis.com/auth/admin.directory.group` scope. Credentials stored
  in SSM at `/javabin/platform/google-admin-sa` as a SecureString. Lambda reads it
  at runtime — never baked into the function.

- **GitHub API** via a GitHub App installed on the javaBin org. Generates short-lived
  tokens automatically — no stored PAT that can expire or leak. Alexander installs the
  app once, stores the private key in SSM at `/javabin/platform/github-app-key`.

- **AWS SDK** — native, uses Lambda execution role. Permissions:
  `cognito-idp:CreateGroup`, `AdminAddUserToGroup`, `AdminRemoveUserFromGroup`,
  `identitystore:CreateGroup`, `CreateGroupMembership`, `DeleteGroupMembership`,
  all scoped to the internal user pool and Identity Center instance.

**Ongoing reconciliation — daily drift detection:**

Team YAML merges handle intentional changes. Heroes join and leave informally throughout
the year. A scheduled Lambda (EventBridge, daily) reads all `teams/*.yaml` from the
registry repo, compares current state of each system against the YAML, and posts a
drift report to `#javabin-infra-alerts` if anything is out of sync:

```
⚠️ Team drift detected
Team:    core
GitHub:  hero.name missing from @javaBin/core
Cognito: hero.name not in core group
Fix:     update registry/teams/core.yaml or correct manually
```

The drift detector does not auto-fix — it reports. Fixes happen via PR (audit trail)
or manual correction. One exception: when a Google account is **suspended**, the
reconciliation Lambda immediately removes that person from GitHub teams and Cognito
groups without waiting for a YAML PR. Suspension is a security event — no lag tolerated.

**AWS resource visibility scoping by team:**

The `JavabinDeveloper` IAM permission set is not plain ViewOnlyAccess. It is a custom
policy that scopes reads to resources tagged with the hero's own team. A `core` hero
cannot see `nettside` resources and vice versa.

Console and API read access — uses the AWS-managed `ReadOnlyAccess` policy as the base
grant, scoped by team using `StringEqualsIfExists`:
```json
{
  "Effect": "Allow",
  "Action": [
    "*:Describe*", "*:Get*", "*:List*",
    "ce:GetCostAndUsage"
  ],
  "Resource": "*",
  "Condition": {
    "StringEqualsIfExists": {
      "aws:ResourceTag/team": "${aws:PrincipalTag/team}"
    }
  }
}
```

`StringEqualsIfExists` means: for any read action that supports resource tags, the
resource must be tagged with the hero's own team. For actions that don't support
resource tags (some List/Describe calls, Cost Explorer), the condition passes and
read access is granted — which is fine, read-only access to untaggable resources
is not a security concern. This covers all current and future AWS services without
enumerating them.

SSM parameter access — scoped by both path and tag (belt and braces):
```json
{
  "Effect": "Allow",
  "Action": [
    "ssm:GetParameter",
    "ssm:GetParameters",
    "ssm:GetParametersByPath"
  ],
  "Resource": "arn:aws:ssm:*:*:parameter/javabin/apps/*",
  "Condition": {
    "StringEquals": {
      "aws:ResourceTag/team": "${aws:PrincipalTag/team}"
    }
  }
}
```

A `nettside` hero running `GetParametersByPath` on `/javabin/apps/` gets back only
parameters tagged `team=nettside`. Parameters tagged `team=core` return access denied —
and since the API returns an empty result rather than a 403 on path enumeration, they
cannot even tell other teams' parameters exist.

Two roles are exempt from the team condition:
- `JavabinAdmin` (platform-owners) — sees everything, needs to manage all resources
- `JavabinReadOnly` (board, pkom oversight) — unconditioned read-only for governance

**`javabin status` and `javabin whoami` filter by team automatically:**

The CLI reads the team claim from the cached Cognito token. All Cost Explorer and ECS
queries are filtered by `team={your-team}`.

**GitHub team → repo access alignment:**

The team YAML `repos` list drives GitHub repo access. Heroes in a team get read access
to those repos via the provisioner. This means GitHub repo visibility and AWS resource
visibility are always in sync — if you can see the repo on GitHub, you can see its AWS
resources in the console and CLI. If you cannot see the repo, you cannot see its
resources either. The mapping is maintained automatically by the provisioner — no
manual GitHub settings to keep in sync.

**Validation at registration time:**

When an app is registered in `registry/apps/{name}.yaml`, the `team` field is validated
against the known teams in `registry/teams/`. An unknown team name fails the PR check
with a clear error. This prevents tags drifting out of sync with the team definitions
and keeps the GitHub team ↔ AWS tag mapping clean across the whole platform.



**The single workflow file every app repo needs:**

This is the entire CI configuration an app repo has to add. One file, five lines.
The platform workflow handles all detection, building, infrastructure, and deployment.

```yaml
# .github/workflows/deploy.yml — the only CI file an app repo needs
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

**Reusable workflows in `javaBin/platform/.github/workflows/`:**

```
javabin.yml          — unified entrypoint, orchestrates everything below
detect.yml           — detects what the repo contains, sets outputs for all other jobs
build-jvm.yml        — Maven build + test, uses JVM CI image from ECR
build-ts.yml         — pnpm install + build, uses TypeScript CI image from ECR
docker-build.yml     — buildah build + ECR push, uses GitHub cache for layers
tf-plan.yml          — terraform init, validate, fmt, plan, upload artifact + SHA to S3
tf-apply.yml         — verify SHA, apply via project role
cdk-synth.yml        — CDK synth + validate tags on CloudFormation template
cdk-deploy.yml       — CDK deploy via project role
eb-deploy.yml        — Elastic Beanstalk deploy (transition period only, until repo migrates to ECS)
ecs-deploy.yml       — update ECS task definition + wait for stable
plan-review.yml      — unified LLM review with cost analysis
```

**Unified CI — no modes, just detection:**

The `detect` job runs first and sets boolean outputs that gate every downstream job.
It detects everything in a single step:

```yaml
jobs:
  detect:
    outputs:
      has_yaml:   ${{ steps.detect.outputs.has_yaml }}
      has_tf:     ${{ steps.detect.outputs.has_tf }}
      has_cdk:    ${{ steps.detect.outputs.has_cdk }}
      has_docker: ${{ steps.detect.outputs.has_docker }}
      has_maven:  ${{ steps.detect.outputs.has_maven }}
      has_pnpm:   ${{ steps.detect.outputs.has_pnpm }}
      has_eb:     ${{ steps.detect.outputs.has_eb }}
      has_env_dirs: ${{ steps.detect.outputs.has_env_dirs }}
    steps:
      - uses: actions/checkout@v4
      - id: detect
        run: |
          echo "has_yaml=$([[ -f app.yaml ]] && echo true || echo false)" >> $GITHUB_OUTPUT
          echo "has_tf=$([[ -n $(find . -name '*.tf' -not -name 'platform.tf') ]] && echo true || echo false)" >> $GITHUB_OUTPUT
          echo "has_cdk=$([[ -f cdk.json ]] && echo true || echo false)" >> $GITHUB_OUTPUT
          echo "has_docker=$([[ -f Dockerfile ]] && echo true || echo false)" >> $GITHUB_OUTPUT
          echo "has_maven=$([[ -f pom.xml ]] && echo true || echo false)" >> $GITHUB_OUTPUT
          echo "has_pnpm=$([[ -f pnpm-lock.yaml ]] && echo true || echo false)" >> $GITHUB_OUTPUT
          echo "has_eb=$([[ -d .elasticbeanstalk ]] && echo true || echo false)" >> $GITHUB_OUTPUT
          echo "has_env_dirs=$([[ -d infra/prod ]] && echo true || echo false)" >> $GITHUB_OUTPUT

  build:
    needs: detect
    if: needs.detect.outputs.has_docker == 'true'
    # Runs maven or pnpm build first (if applicable), then docker build + ECR push
    # Steps share the same runner — checkout → build → docker → push all in one job
    # Maven: uses actions/cache for ~/.m2, JVM custom CI image
    # pnpm: uses actions/cache for ~/.pnpm-store, TypeScript custom CI image
    # Docker layers: cache-from/cache-to type=gha (GitHub cache, not ECR — zero ECR storage cost)

  infra-plan:
    needs: detect
    if: needs.detect.outputs.has_tf == 'true' || needs.detect.outputs.has_yaml == 'true' || needs.detect.outputs.has_cdk == 'true'
    # Parallel: tf-plan + cdk-synth, outputs merged for review

  review:
    needs: [build, infra-plan]
    if: always() && (needs.build.result != 'skipped' || needs.infra-plan.result != 'skipped')
    # LLM risk + cost analysis, posts single PR comment
    # Reads project budget from platform-registry YAML

  deploy:
    needs: review
    if: github.ref == 'refs/heads/main' || github.ref == 'refs/heads/master'
    # if has_eb → eb-deploy (transition until repo migrates to ECS)
    # if ECS task definition exists → ecs-deploy
    # if has_tf → tf-apply (SHA verified)
    # if has_cdk → cdk-deploy
    # Multiple deploy targets run if multiple exist (e.g. tf-apply + ecs-deploy)
```

**Elastic Beanstalk support (transition period):**

Repos with `.elasticbeanstalk/` folders continue deploying to EB via the `eb-deploy` job.
This means cake-redux and moresleep get the golden path CI (plan review, cost analysis,
tags, attribution) immediately, even before ECS migration. EB deploy is removed from the
workflow once the repo's app.yaml or ECS task definition is present and ECS deploy is
confirmed working. Do not remove EB deploy until the developer confirms migration.

**Elastic Beanstalk explained for context:**
EB is AWS's original managed platform (2011) — give it a Dockerfile or JAR and it provisions
EC2 instances, load balancer, and auto-scaling automatically. It is opaque, uses older EC2
infrastructure, and is being superseded by ECS/Fargate. Both cake-redux and moresleep use it.
Migrating them to ECS is the right call — they already have Dockerfiles so the app side is
done, it's just swapping the deploy target and adding ECS task definitions.

**Direct push support:**
The workflow triggers on both `pull_request` and `push` to main/master. Plan review output
is posted as a PR comment when a PR exists. When there is no PR (direct push to main), the
review output is routed to `#javabin-infra-alerts` Slack instead. The risk gate and override
mechanism work identically for both — if risk is HIGH, `tf-apply.yml` checks SSM for an
override token regardless of whether the push came from a PR or direct to main. The blocked
Slack message includes the pre-filled override link either way.

**Plan review Lambda — unified with cost analysis:**

The review Lambda receives outputs from whichever plan/synth jobs ran and produces
a single PR comment covering both risk and cost:

```
## Platform Review

Risk:  LOW ✅
Cost:  +420 NOK/month (↑ from ~180 NOK to ~600 NOK)

New resources:
  aws_ecs_service.cake-redux        +290 NOK/mo  (0.5 vCPU, 1GB)
  aws_rds_instance.cake-redux-db    +130 NOK/mo  (db.t3.micro)

Analysis: Standard ECS + RDS pattern. RDS in private subnet, SG restricts
access to ECS tasks only. No public exposure. Cost increase is expected
for this service type.

Budget status: 600 NOK estimated vs 1000 NOK threshold — within budget ✅
```

Cost analysis implementation:
- Parse plan/changeset for resource creates, modifies, destroys
- Call AWS Pricing API for each resource type (same logic already in slack-alert Lambda)
- Compute monthly delta — what this plan adds or removes from the bill
- Read project budget from registry YAML (passed as input to review Lambda)
- LLM receives plan JSON + pricing data + current budget as combined context
- LLM comments on cost proportionality, flags expensive instance choices,
  warns if estimated total would breach project budget

**Shared pricing utility:**
Extract the Pricing API logic from `slack-alert` into a shared Lambda layer or
utility module. Both `slack-alert` and the plan review Lambda use it. Do not
maintain pricing lookup logic in two places.

**app.yaml (optional contract file):**
```yaml
# app.yaml — optional, place in repo root
name: cake-redux
team: pkom

compute:
  cpu: 512
  memory: 1024
  port: 8080

database:             # optional
  type: postgres
  size: small

auth: internal        # internal | external | both | none

domain: cake-redux.java.no

budget_alert_nok: 500  # optional, defaults to 1000 NOK

# Optional: add a dev environment. If omitted, only prod exists.
environments:
  prod:
    compute: { cpu: 512, memory: 1024 }
    domain: submit.javazone.no
  dev:                           # add this block to get a dev environment
    compute: { cpu: 256, memory: 512 }
    domain: dev.submit.javazone.no
    auto_deploy: true            # deploys on every merge to main
    budget_alert_nok: 200
```

**Dev/prod via directory convention (alternative to app.yaml environments block):**

For repos using raw Terraform, the convention is directory-based. The `detect` job checks
for `infra/prod/` — if `infra/dev/` also exists, both environments get their own plan/apply.
Developer opts into dev by creating the directory. Opts out by not having it. No tooling
required to understand the model.

```
infra/
  prod/
    main.tf        # or app.yaml equivalent
  dev/             # optional — create this directory to get a dev environment
    main.tf
```

Dev and prod get separate IAM roles scoped to their GitHub Environment:
```
javabin-ci-deploy-{repo-name}-dev   (trust: environment:dev)
javabin-ci-deploy-{repo-name}-prod  (trust: environment:prod, requires board approval for conference-facing)
```

**Rules for when to add dev:**
- Internal tools (cake-redux, moresleep, cupcake): single prod only. Rollback is the safety net.
- Conference-facing (submittheforce, javazone.no, native app): dev required. Broken CFP or
  conference site during live events causes real damage.
- Developer's own projects: their choice.

If `app.yaml` is present:
- CI generates `platform.tf` (state backend config, provider with injected tags, module calls
  for each defined resource). Generates one per environment if environments block exists.
- CI regenerates on every run and fails if checked-in version diverges (clear error message)
- Developer adds extra `.tf` files freely alongside it for things app.yaml doesn't support
- CI validates plan output has required tags on all resources

If no `app.yaml`:
- CI generates only `platform.tf` minimum (state config + provider with injected tags)
- Developer writes all Terraform themselves in their own `.tf` files
- Same plan/review/apply pipeline runs

If CDK repo (cdk.json detected):
- CI runs cdk synth, validates tags on CloudFormation template output
- Runs LLM review against changeset
- Deploys via project role
- CDK and Terraform can coexist in the same repo — both paths run

**ECR lifecycle policy (applied to every ECR repo at registration, automatic):**

```json
[
  {"rulePriority": 1, "description": "Keep last 5 semver releases",
   "selection": {"tagStatus": "tagged", "tagPrefixList": ["v"],
                 "countType": "imageCountMoreThan", "countNumber": 5},
   "action": {"type": "expire"}},
  {"rulePriority": 2, "description": "Keep last 3 main branch builds",
   "selection": {"tagStatus": "tagged", "tagPrefixList": ["main-", "master-"],
                 "countType": "imageCountMoreThan", "countNumber": 3},
   "action": {"type": "expire"}},
  {"rulePriority": 3, "description": "Expire untagged after 1 day",
   "selection": {"tagStatus": "untagged", "countType": "sinceImagePushed",
                 "countUnit": "days", "countNumber": 1},
   "action": {"type": "expire"}},
  {"rulePriority": 4, "description": "Expire all other tags after 7 days",
   "selection": {"tagStatus": "tagged", "countType": "sinceImagePushed",
                 "countUnit": "days", "countNumber": 7},
   "action": {"type": "expire"}}
]
```

Image tagging convention:
```
sha-{git-sha}          # every build — primary identifier, what ECS task definitions reference
main-{yyyyMMdd-HHmm}   # builds from main — human readable
v1.2.3                 # semver release tags
latest                 # points to most recent main build (convenience only, never used in task defs)
```

ECS task definitions always reference `sha-{git-sha}` — never `latest`. You always know
exactly what version is running. Docker layer caching uses `cache-from/cache-to: type=gha`
(GitHub's own cache storage) — zero ECR storage cost for cache data.

**javabin CLI:**
A CLI tool called `javabin` — not a Terraform wrapper, not a deploy tool. Its purpose is
registration, status, and identity. CI owns plan/apply/deploy entirely. The CLI is named
`javabin` and feels like a first-class Javabin product.

Commands:
- `javabin register` — interactive wizard that opens a registration PR against
  `javaBin/registry`. Asks for repo name, team, and whether a dev environment
  is needed. Creates the `apps/{name}.yaml` file in the right format, opens the PR via
  GitHub API. This is the one flow complex enough to warrant a CLI — it knows the
  registry format, knows how to open PRs, and gives a good first-run experience.
- `javabin status` — shows current AWS spend this month for your project (Cost Explorer),
  running ECS services, recent deployments, and resource count. Useful without opening
  the console. Also shows if any resources are untagged (compliance view).
- `javabin whoami` — shows current AWS identity, assumed role, project scope, and
  which @java.no account is active. Debugging aid for when things aren't working.

Authentication: lazy device flow — the CLI does not require explicit login. Any command
that needs AWS or identity context checks for a valid cached token first. If none exists
or the token is expired, the device flow against the internal Cognito User Pool (via the
hero's @java.no Google account) triggers automatically inline, completes, caches the token,
and the original command proceeds. The user never has to run a separate login step.

Token cached locally in `~/.javabin/token`. No AWS credentials stored by the CLI —
AWS calls use the standard credential chain (IAM Identity Center, environment variables,
or the assumed project role depending on context).

Do NOT add plan, apply, generate, validate, or any deploy commands to the CLI.
Those belong in CI. If a command doesn't justify being easier than going to GitHub
or the AWS console directly, don't add it.

---

### Phase 4 — Onboard existing apps

**Goal:** Every active repo gets the golden path CI with minimal effort from the developer.
The platform does the heavy lifting. The developer adds one file and opens one PR.

**Onboarding steps for any existing repo:**

Step 1 — Add the workflow file (the same five-line `deploy.yml` shown above).

Step 2 — Open a registration PR to registry:
```yaml
# registry/apps/cake-redux.yaml
name: cake-redux
team: pkom
repo: javaBin/cake-redux
existing: true        # signals this is an import, not greenfield — triggers discovery
```

The `existing: true` flag tells the provisioning workflow to run AWS discovery before
creating anything. It queries existing resources tagged or named for this project,
lists them in a Slack message for the infra owner to review, and only creates what
is genuinely missing. It never duplicates existing resources.

Step 3 — Board member merges registration PR. Platform-infra provisioning runs:
- Creates project IAM roles (scoped, with permission boundary)
- Creates AWS Budget with default 1000 NOK threshold
- Runs resource discovery: `aws resourcegroupstaggingapi get-resources` filtered by
  repo name and known EB environment names. Posts findings to `#javabin-infra-alerts`.
- Creates SSM parameter namespace `/javabin/apps/{name}/`
- Does NOT touch existing EB environments, CloudFormation stacks, or running resources

Step 4 — CI is live. From the first PR after merging the workflow file:
- If `.elasticbeanstalk/` exists → EB deploy path runs automatically (no changes needed)
- Plan review, cost analysis, CloudTrail attribution all work immediately
- Tags are injected on any new resources created going forward
- Developer can continue working exactly as before while migration happens at their pace

Step 5 — ECS migration (happens when the developer is ready, not on a deadline):
- Add `app.yaml` or ECS task definition to the repo
- CI detects ECS path, runs both EB and ECS deploy in parallel on first run
- Developer verifies ECS is working, then removes `.elasticbeanstalk/` folder
- EB environment is decommissioned (manual step, developer confirms)
- Remove `.travis.yml` from cake-redux (dead CI config, creates confusion)

**Priority order for onboarding:**
1. First — use a low-stakes throwaway repo as the platform proof of concept. Either create
   `javaBin/platform-test` specifically for this purpose, or Alexander picks a simple existing
   repo that has a Dockerfile but isn't a critical service. Do not use cake-redux or moresleep
   as the first test — they are production services and should only be migrated once the
   platform is verified working end-to-end on a non-critical repo first.
2. Once the proof of concept is verified: `moresleep` — simpler stack, good second candidate.
3. `cake-redux` — higher complexity (CDK + Maven + Docker + EB all at once), do this third.
4. `submittheforce`, `javazone.no` — conference-facing, add dev environment when migrating.
5. Remaining active repos in order of recent commit activity.
6. Leave archived/legacy year-specific repos (javazone-2020, androidito etc) alone.

**What "approved repo" means technically:**
The OIDC trust policy on each project role is generated from the registry.
If a repo is not in registry, there is no IAM role for it to assume.
CI gets an authentication error from AWS. The registry PR is not just a process gate —
it is the IAM gate. You cannot accidentally deploy from an unregistered repo because
AWS rejects the credential request. No additional enforcement needed beyond the registry.

Do not delete existing EB infrastructure until ECS is confirmed working.
Do not delete CDK stacks until Terraform state has imported or replaced those resources.

---

## Technical constraints and guardrails

CI always enforces:
1. Tags `project` and `team` on all resources (injected via provider default_tags, Config rule as backup)
2. Plan artifact SHA256 verified before apply
3. OIDC trust policy restricts role assumption to registered repos
4. Permission boundary self-replicating condition prevents escalation
5. `job_workflow_ref` on deploy roles — only platform workflows get credentials

---

## LLM risk gate and override mechanism

The LLM plan review is a hard gate on deployment. When the review returns HIGH risk,
`tf-apply.yml` does not proceed. The `job_workflow_ref` condition on deploy roles
(defined in Phase 2) means this gate cannot be bypassed from the app repo.

**What HIGH risk looks like (examples the LLM should flag):**
- RDS instance with `publicly_accessible = true`
- Security group with `0.0.0.0/0` ingress on sensitive ports
- S3 bucket with public ACL or no block-public-access
- IAM policy with `*:*` without boundary reference
- Instance type jump from t3.micro to r6g.8xlarge (cost explosion)
- Resource count increase that would breach the project budget
- Missing or incorrect tags on any resource in the plan

**When a deploy is blocked, `tf-apply.yml` posts to `#javabin-infra-alerts`:**
```
🚫 Deploy blocked — HIGH risk
Repo:    javaBin/cake-redux
Commit:  abc1234  "Add RDS instance"
Actor:   @hero-username
Finding: RDS instance has publicly_accessible=true. Potential data exposure.
         Cost delta: +3,200 NOK/month (budget: 1,000 NOK). Would exceed budget by 3x.

To request an override:
→ https://github.com/javaBin/platform/actions/workflows/approve-override.yml
  ?inputs[repo]=cake-redux&inputs[sha]=abc1234
```

The link lands the admin directly on the workflow_dispatch page in platform with
`repo` and `sha` pre-filled. The admin types a reason and taps Run. That is the entire
action required from a phone.

**The override workflow — `platform/.github/workflows/approve-override.yml`:**

```yaml
name: Approve Deploy Override
on:
  workflow_dispatch:
    inputs:
      repo:
        description: 'App repo name (e.g. cake-redux)'
        required: true
      sha:
        description: 'Commit SHA to unblock'
        required: true
      reason:
        description: 'Reason for override (logged permanently)'
        required: true

jobs:
  approve:
    runs-on: ubuntu-latest
    steps:
      - name: Assume override-approver role
        uses: aws-actions/configure-aws-credentials@v4
        with:
          role-to-assume: arn:aws:iam::{account}:role/javabin-ci-override-approver
          aws-region: eu-west-1
        # AWS checks:
        #   job_workflow_ref == approve-override.yml@refs/heads/main  ✓
        #   actor is in the override_approvers list                   ✓
        # If either fails → 403 from STS → job fails → no SSM write

      - name: Write approval token to SSM
        run: |
          aws ssm put-parameter \
            --name "/javabin/overrides/${{ inputs.repo }}/${{ inputs.sha }}" \
            --value "approved:${{ github.actor }}:${{ inputs.reason }}:$(date -u +%s)" \
            --type SecureString \
            --overwrite

      - name: Post to Slack
        run: |
          # Post to #javabin-infra-alerts:
          # "✅ Override approved — javaBin/{repo} @ {sha}
          #  By: @{actor}
          #  Reason: {reason}"
```

The `javabin-ci-override-approver` IAM role has `ssm:PutParameter` on
`/javabin/overrides/*` only. It cannot do anything else. Even if the actor check
somehow passed for an unintended person, the blast radius is a single SSM write
scoped to the overrides path.

**How `tf-apply.yml` consumes the override token:**

```yaml
- name: Check risk gate
  run: |
    if [[ "$RISK_LEVEL" == "HIGH" ]]; then
      OVERRIDE=$(aws ssm get-parameter \
        --name "/javabin/overrides/${{ github.event.repository.name }}/${{ github.sha }}" \
        --with-decryption \
        --query Parameter.Value \
        --output text 2>/dev/null || echo "none")

      if [[ "$OVERRIDE" == none ]]; then
        echo "❌ Blocked by LLM review (HIGH risk). No override token found."
        echo "Request an override at: https://github.com/javaBin/platform/actions/workflows/approve-override.yml"
        exit 1
      fi

      echo "⚠️ Risk gate overridden — proceeding with deployment"
      echo "Override: $OVERRIDE"
    fi

- name: Delete override token after use
  if: always()
  run: |
    aws ssm delete-parameter \
      --name "/javabin/overrides/${{ github.event.repository.name }}/${{ github.sha }}" \
      2>/dev/null || true
    # Tokens are single-use. Even if unused, a cleanup Lambda
    # deletes any token older than 24 hours as a backstop.
```

Override tokens are single-use and deleted immediately after the apply that consumed them.
A cleanup Lambda (EventBridge scheduled, every hour) deletes any token older than 24 hours
as a backstop — an admin cannot pre-approve a SHA for future use.

**Terraform variable for the actor allowlist:**
```hcl
# platform/terraform/platform/variables.tf
variable "override_approvers" {
  description = "GitHub usernames permitted to approve risk gate overrides"
  type        = list(string)
  default     = ["alexander-amiri"]
  # Add board members and infra leads here.
  # Changing this list requires a PR to platform reviewed by platform-owners.
}
```

The IAM trust policy for `javabin-ci-override-approver` references this variable.

---

## Cost tracking architecture

Per-project visibility:
- Every resource tagged `project={repo-name}` by CI (automatic, no developer action)
- AWS Budgets: one Budget per registered project, scoped to `project` tag filter
  - Default threshold: 1000 NOK/month
  - Alert → `#javabin-cost-alerts` Slack channel
  - Budget created automatically as part of app registration
- Cost Explorer saved filters: one per project, created at registration
- Weekly cost-report Lambda: break down by project tag in addition to service breakdown

Platform-level costs (VPC, ALB, NAT, shared services):
- Tagged `project=platform`, `team=infra`
- Separate budget for platform infrastructure
- Reported separately in weekly cost report

---

## Platform-infra repo protection

**Repo visibility:** Private. Being a javaBin org member does not grant access.
Non-profit status does not restrict private repos — this is fully available on Team plan.

**GitHub team:** Create `@javaBin/platform-owners` — board members and whoever
is trusted with platform changes. This team gets `maintain` access. All other org members
get read access or none.

**Branch Rulesets on main:**
- Require PRs (no direct push to main)
- Required reviewer: at least one member of `@javaBin/platform-owners` who is
  not the PR author — board members cannot self-merge platform changes
- Require status checks to pass (plan + LLM review workflow)
- Block force pushes
- Block branch deletion
- Restrict pushers: only `@javaBin/platform-owners`

**CODEOWNERS:**
```
# .github/CODEOWNERS
*                   @javaBin/platform-owners
terraform/org/      @javaBin/board
terraform/iam/      @javaBin/platform-owners
terraform/monitoring/ @javaBin/platform-owners
```

Any PR touching `terraform/org/` (SCPs, Organizations) requires a board member approval
regardless of who opens it. This means org-level changes always have two sets of eyes
and can never be merged by a single person acting alone.

**Split Terraform root modules:**
```
terraform/
  platform/   # CI applies — VPC, ECS, IAM roles, monitoring, Cognito, lambdas
  org/        # humans only — AWS Organizations, SCPs, Identity Center config
```

The `org/` module has no CI pipeline. Applying it requires a human with `JavabinAdmin`
Identity Center credentials running Terraform locally. It is still version-controlled
and reviewed via PRs — the review gate is enforced by CODEOWNERS — but the apply
is deliberately manual. A compromised CI pipeline cannot touch org structure or SCPs.

**Scoping the `javabin-ci-infra` role:**

The infra CI role uses `Allow *:*` with the permission boundary, same as the reference
implementation. However it gets an additional explicit deny block for things that should
only ever happen via a human — not because the boundary doesn't cover them, but because
CI explicitly should never do these even if somehow the boundary were misconfigured:

```json
{
  "Effect": "Deny",
  "Action": [
    "organizations:*",
    "account:*",
    "iam:CreateUser",
    "iam:CreateAccessKey",
    "iam:CreateLoginProfile",
    "identitystore:CreateUser",
    "sso:CreateInstance",
    "sso:DeleteInstance",
    "cloudtrail:DeleteTrail",
    "cloudtrail:StopLogging",
    "cloudtrail:UpdateTrail",
    "guardduty:DeleteDetector",
    "securityhub:DisableSecurityHub",
    "config:DeleteConfigurationRecorder",
    "config:StopConfigurationRecorder",
    "s3:DeleteBucket"
  ],
  "Resource": "*",
  "Sid": "InfraCIHardDenies"
}
```

Note: `organizations:*` here means the CI role cannot create or modify SCPs even if
someone adds org Terraform to the platform module. The org/ module must be applied
manually — this deny makes that structurally enforced, not just a convention.

**Recovery runbook (store outside the repo — board-member-shared Google Doc):**
- How to apply `terraform/platform/` manually with Identity Center credentials
- How to apply `terraform/org/` manually
- How to rotate the state bucket access if needed
- Who has `JavabinAdmin` Identity Center access

## GitHub non-profit — applying for Enterprise Cloud

Javabin almost certainly already has the free Team plan from existing non-profit status.
Enterprise Cloud adds SAML SSO enforcement (org members must authenticate via Google
Workspace — a suspended Google account means automatic org removal).

At 25% off, Enterprise Cloud costs ~$15.75/user/month. For 70 heroes that is ~$1,100/month.
Apply at github.com/solutions/industry/nonprofits — costs nothing to apply, then decide
whether to redeem once you know if SAML SSO enforcement is worth the monthly cost.

## Custom CI images

Store in ECR under `javaBin/platform`, rebuild monthly via scheduled workflow.

**JVM image** (cake-redux, moresleep, cupcake, annual-meeting-membership):
- Base: `eclipse-temurin:21-jdk-alpine`
- Pre-warmed Maven wrapper and common Spring Boot / Ktor dependencies baked in
- Eliminates 100–200MB download on cold builds
- Rebuilt when base image updates or dependency versions change

**TypeScript image** (java.no, 2025.javazone.no-native):
- Base: `node:22-alpine`
- pnpm pre-installed via corepack
- Rebuilt monthly

**Platform image** (used by all plan/apply jobs):
- Base: `hashicorp/terraform:1.x`
- AWS CLI, jq, python3 pre-installed
- AWS Terraform provider pre-mirrored into the image (~50MB download avoided per run)
- `javabin` CLI binary pre-installed
- This is the most impactful image for CI speed — provider mirror is the single biggest
  Terraform startup cost

**Runner strategy:** GitHub-hosted runners only (2 vCPU, 7GB RAM, Ubuntu 22.04).
Self-hosted runners would share the Coolify EC2 with production services. Not worth the
operational overhead for a volunteer org. Revisit only if builds regularly exceed 10 minutes.

**Caching:** Maven via `actions/cache` on `~/.m2`, pnpm on `~/.pnpm-store`,
Docker layers via `cache-from/cache-to: type=gha`.

## Dev and prod environments

No existing Javabin service has a staging environment. Add it selectively based on
whether a broken deploy causes real damage to heroes or conference attendees.

**Internal tools (cake-redux, moresleep, cupcake, annual-meeting-membership):**
Single production environment. Invest in fast rollback instead: golden path ECS deploy
automatically retains the previous two task definition revisions. One-command rollback
always available. No staging needed.

**Conference-facing services (javazone.no, submittheforce, native app):**
Staging required. Broken CFP during submission week or broken conference site during
ticket sales causes real damage.

Deployment model:
```
PR         → plan/review only, no deploy
main merge → auto-deploy to staging
tag v*.*   → requires board approval → deploy to production
```

GitHub Environments — `staging` (no reviewers, auto-deploys) and `production`
(required board reviewer, triggered by semver tag). OIDC role trust scopes to environment,
so the production role literally cannot be assumed without the environment gate passing.

Staging infra: 256 CPU / 512MB ECS task, separate RDS instance or schema prefix,
same internal Cognito pool with different app client. Costs ~100–150 NOK/month extra.

**Rollout order:**
1. cake-redux, moresleep — single prod, during EB→ECS migration
2. submittheforce, javazone.no — staging + prod, when they go through golden path adoption

## GitHub org README and supporting repos

The org README lives in `javaBin/.github` repo, file `profile/README.md`. This is
public-facing and shows on the github.com/javaBin org page. Keep it slim — orientation
and links only, no architecture, no secrets, no internal detail.

**Create these repos as part of platform setup:**
- `javaBin/.github` — org profile repo, contains `profile/README.md`
- `javaBin/docs` — platform documentation repo (can repurpose existing docs repo if one exists).
  Must have sections on: how the platform works, how to register an app, how auth works
  (internal Cognito pool, what `auth: internal` gives you), how to use the javabin CLI,
  and how to request access. This is the destination for all the links in the README.
- `javaBin/app-template` — a GitHub template repo. Heroes click "Use this template" to
  start a new project already correctly structured. Contains:
  - `.github/workflows/deploy.yml` (the five-line entrypoint)
  - `app.yaml` with placeholder values and inline comments explaining each field
  - A minimal `Dockerfile`
  - A `README.md` explaining the next steps (register via registry, install CLI)
- `javaBin/registry` — already described in registry section above
- `javaBin/javabin-cli` — the CLI source repo

Org README should cover: what Javabin is, how to contribute, how to become a hero,
links to `javaBin/docs`, `javaBin/registry`, `javaBin/app-template`, auth docs,
and CLI install command.

---

## javabin CLI — implementation details

**Language:** Go. Single static binary, no runtime dependencies, easy cross-compilation.

**Distribution:**
```bash
# macOS / Linux — recommended
brew install javaBin/tap/javabin

# Direct install script (Linux, CI, Windows via WSL)
curl -fsSL https://cli.java.no/install.sh | sh

# Go toolchain
go install github.com/javaBin/javabin-cli@latest
```

Host the install script at `cli.java.no` (Route53 + S3 or CloudFront) rather than a raw
GitHub URL. More stable, looks intentional, easy to update without changing the install
command heroes have memorised.

**Release pipeline:**
GoReleaser in GitHub Actions on semver tags. Publishes to GitHub Releases and auto-updates
`javaBin/homebrew-tap`.

**Commands:**

`javabin register`
Interactive wizard. Prompts for:
- Repo name (validates it exists in javaBin org via GitHub API)
- Team (shows available teams from Google Workspace groups)
- Whether a dev environment is needed
- Budget alert threshold (or accept default 1000 NOK)
- Auth requirement (internal / external / both / none)

Creates `apps/{name}.yaml` in the correct format, opens a PR against
`javaBin/registry` via GitHub API using the hero's token. Prints the PR URL
on completion. Requires the hero to have a @java.no GitHub-linked account or a GitHub
token — prompts for GitHub auth separately from Cognito auth if needed.

`javabin status`
Shows for the current project (inferred from git remote or `--project` flag):
- AWS spend this month vs budget threshold (Cost Explorer)
- Running ECS services and their task counts
- Last 5 deployments with commit SHA, actor, and timestamp
- Any untagged resources (compliance view)
- Current month cost trend vs last month

`javabin whoami`
Shows:
- Cognito identity (name, email, groups)
- Current AWS role being assumed (from credential chain)
- Project scope (from principal tags)
- Token expiry

**Docs repo section on CLI** (agent must create this in `javaBin/docs`):
The docs repo should have a `cli.md` or `README.md#cli` section covering installation,
all three commands with examples, and the auth flow. This is what the org README links to.

---

## Slack

Leave the Slack workspace as-is. Do not enforce SAML SSO org-wide (would require
every partner and community member to have a @java.no account).

- Enforce 2FA for Workspace Owners and Admins only
- Workspace Admins should use their @java.no email as primary on their Slack account



- Vaultwarden replacement (SSM handles application secrets; Bitwarden Teams handles human
  credentials — recommend applying for Bitwarden non-profit discount, ~20 free seats)
- Authentik SSO portal (Cognito covers the app auth use case; revisit when you need a
  self-service app portal or richer access policies)
- GCP project (no reason to add a third cloud; Google Workspace OAuth clients are free
  without a GCP project for basic use cases)
- GitHub Enterprise Cloud for SAML SSO (too expensive; org 2FA + OIDC per-role trust is sufficient)
- Full external attendee/speaker identity implementation (create the external Cognito pool
  as a foundation, but "mine sider" and conference login are separate projects)
- Replacing Coolify (leave it running; it serves as the "easy tier" for heroes who want to
  run a simple container without the full golden path)
- Migrating or cleaning up Confluence (out of scope for infrastructure work)

---

## Open questions to resolve before or during implementation

1. **What region is the existing AWS account operating in?**
   Discovery will reveal this. Plan is written assuming eu-west-1 but adapt to whatever exists.

2. **Is there an existing VPC or is the Coolify EC2 in the default VPC?**
   If default VPC, create a proper VPC and migrate Coolify. If custom VPC, import it.

3. **What Slack workspace is used for alerts?**
   Need webhook URLs for `#javabin-infra-alerts` and `#javabin-cost-alerts`.
   Store in SSM as `/javabin/slack/infra-alerts-webhook` and `/javabin/slack/cost-alerts-webhook`.

4. **Is Bedrock available in the target region?**
   If not, use us-east-1 cross-region inference or switch to a different model.
   The LLM review Lambda and cost report narrative generation depend on this.

5. **What is the existing Coolify EC2 instance type and what else runs on it?**
   Need to know before potentially resizing or migrating to accommodate future services.

7. **What EB environments currently exist for cake-redux and moresleep?**
   Discovery will find them. Note their environment names, configuration, and current
   instance types before migrating. Do not terminate until ECS is confirmed working.

8. **Does cake-redux's CDK folder contain infrastructure that is actively deployed?**
   Check if there are live CloudFormation stacks from it. If so, import them into
   Terraform state before the CDK folder is migrated.

---

## Immediate first steps (in order)

The goal is to get everything into Terraform and into the repo as fast as possible,
and to hand off to CI as fast as possible after that. Steps 1–5 are the only ones
that involve any manual AWS CLI work. Everything from step 6 onward is Terraform,
committed to the repo, applied manually until CI exists, then via pipeline.

1. Run discovery (list all existing AWS resources — see discovery section above)
2. Create `javaBin/platform` repo (private) — add `.gitignore` with `*.tfstate`,
   `*.tfstate.backup`, `.terraform/`, `*.tfplan` before any other commit
3. Create `javaBin/registry` repo (private, org-visible) with `apps/` and `teams/`
   directories, placeholder README
4. Run `platform/scripts/bootstrap.sh` to create state bucket and lock tables
   (the only manual CLI step — see bootstrapping section in Phase 0 for the full script)
5. Write Terraform for the state resources in `terraform/state/`, import them,
   migrate state to S3 (`terraform init -migrate-state`), commit — repo is now source
   of truth even before CI exists
6. Write and apply (manually): permission boundary policy, GitHub OIDC provider,
   `javabin-ci-infra` role — commit each before applying
7. Wire GitHub Actions to assume `javabin-ci-infra` — **this is the CI handoff point**.
   Test with a plan-only run. From this point, platform resources go through the pipeline.
   No more manual `terraform apply` for platform except `terraform/org/`.
8. Write and apply via CI: VPC, subnets, NAT, ALB, ACM certs, Route53 import
9. Write and apply via CI: monitoring Lambda stack (port from reference, adapt slack-alert
   to read GitHub OIDC session tags, extract shared pricing utility, add cost analysis to plan review)
10. Enable via CI: Security Hub, GuardDuty, Config
11. Write and apply via CI: IAM Identity Center + Google SAML connection
    (Alexander configures Google side in parallel)
12. Write and apply via CI: Cognito pools (internal + external)
13. Write and apply via CI: ECS cluster, ECR base config
14. Build reusable CI workflows in `javaBin/platform/.github/workflows/` including
    unified `javabin.yml` entrypoint, EB support, override mechanism
15. Build and push custom CI images (JVM, TypeScript, platform) to ECR via CI
16. Create Google Workspace service account with domain-wide delegation for
    team-provisioner — store in SSM at `/javabin/platform/google-admin-sa`
17. Create GitHub App on javaBin org for team-provisioner — store private key in SSM
    at `/javabin/platform/github-app-key`
18. Write and deploy via CI: team-provisioner Lambda and daily drift-detection Lambda
19. Create initial team YAMLs in registry (`core`, `nettside`, `pkom`, `platform-owners`)
    and verify provisioning runs end-to-end across all four systems
    (Google group created, GitHub team created, Cognito group created, IAM tag flowing)
20. Register `javaBin/platform-test` (or Alexander nominates a low-stakes existing repo)
    as the first app proof of concept via registry
21. Verify plan review, cost analysis, CloudTrail attribution, Slack alerts, direct-push
    support, risk gate + override flow, and EB deploy path all work end-to-end before
    onboarding any production repos

Do not proceed to Phase 3 or 4 until Phase 0 and Phase 1 are verified working,
existing admin access is confirmed unaffected, and CI is handling all platform applies.
