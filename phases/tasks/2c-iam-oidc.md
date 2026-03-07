# Task 2c — IAM / OIDC Module (GitHub OIDC + CI Roles)

**Status:** Not started
**Phase:** 2 (Platform Infrastructure)
**Depends on:** 0c (permission boundary must exist)
**Blocks:** 2g (CI handoff needs the infra role)

## Objective

Register the GitHub Actions OIDC provider and create the four CI roles that enable
the entire platform's CI/CD pipeline.

## Context

Read `phases/phase-2-platform.md` Task 3 for full spec.
Read `PLAN.md` sections on IAM/OIDC and tag-based resource isolation.
Reference: `terraform/iam/` (copied from ai-platform-infra — uses GitLab, we use GitHub).

**Critical difference from reference:** No ci-authorizer Lambda. GitHub's `job_workflow_ref`
OIDC claim replaces the custom OIDC provider. This is simpler and sufficient.

## Deliverables

### `terraform/platform/iam/`
- `main.tf` — OIDC provider, 4 CI roles, ECS execution role
- `boundary.tf` — Permission boundary (from task 0c, already exists)
- `variables.tf` — project, region, account_id, registered_app_repos, override_approvers
- `outputs.tf` — role ARNs, execution role ARN, OIDC provider ARN

## Spec: GitHub OIDC Provider

An OIDC provider for `token.actions.githubusercontent.com` already exists (managed by the
`GithubOIDC` CloudFormation stack, trusts 8 repos). AWS only allows ONE OIDC provider per
issuer URL per account. We must use a `data` source to reference the existing provider —
we cannot create a new one. Our CI roles trust the same provider but with different conditions.

```hcl
# Cannot create — already exists. Reference it.
data "aws_iam_openid_connect_provider" "github" {
  url = "https://token.actions.githubusercontent.com"
}
```

## Spec: Four CI Roles

### 1. `javabin-ci-infra`
- **Trust:** GitHub OIDC, `repo:javaBin/platform:ref:refs/heads/main`
- **Permissions:** Allow `*:*` + explicit deny block (orgs, IAM users, CloudTrail, GuardDuty, etc.)
- **Boundary:** Yes
- **Purpose:** Platform CI plan/apply. THIS IS THE CI HANDOFF ROLE.

### 2. `javabin-ci-app-{repo-name}` (one per registered app)
- **Trust:** GitHub OIDC with TWO conditions:
  - `sub` matches `repo:javaBin/{repo-name}:*`
  - `job_workflow_ref` = `javaBin/platform/.github/workflows/tf-plan.yml@refs/heads/main`
- **Permissions:** Allow `*:*` with tag-based isolation (`StringEqualsIfExists` on `project` tag)
- **Boundary:** `javabin-developer-boundary`
- **Session tags:** project, GitHubActor, GitHubSHA, GitHubRepo, GitHubRunID

### 3. `javabin-ci-deploy-{repo-name}` (one per registered app)
- **Trust:** GitHub OIDC with `job_workflow_ref` = `javaBin/platform/.github/workflows/tf-apply.yml@refs/heads/main`
- **Permissions:** ECR push (repo-specific), ECS update service, CloudWatch logs
- **Boundary:** `javabin-developer-boundary`
- **Critical lock:** App repos cannot write workflows that assume this role

### 4. `javabin-ci-override-approver`
- **Trust:** GitHub OIDC with TWO conditions:
  - `job_workflow_ref` = `javaBin/platform/.github/workflows/approve-override.yml@refs/heads/main`
  - `actor` in `var.override_approvers` (default: `["alexander-amiri"]`)
- **Permissions:** `ssm:PutParameter` on `/javabin/overrides/*` only
- **Purpose:** Risk gate override for HIGH-risk deploys

## Spec: ECS Execution Role
- Name: `javabin-ecs-execution`
- Trust: ECS tasks service principal
- Permissions: ECR pull, CloudWatch Logs write, Secrets Manager read for `javabin/*`

## Rules
- No ci-authorizer Lambda — `job_workflow_ref` replaces custom OIDC
- Permission boundary on ALL CI roles (including infra role)
- App roles created dynamically from `var.registered_app_repos` list
- Tag-based isolation uses `StringEqualsIfExists` — not service-specific deny blocks
- The `project` principal tag is set as STS session tag from `github.repository`
- Override approvers list is a Terraform variable — changing it requires a platform PR
