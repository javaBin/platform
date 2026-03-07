# Org Module Runbook

The `terraform/org/` module manages AWS Organizations and Service Control Policies (SCPs). It is deliberately excluded from CI -- only humans apply it.

## Why no CI pipeline

The org module controls the organization structure and SCPs. A compromised CI pipeline must not be able to:
- Modify SCPs (which could remove the permission boundary requirement)
- Change the organization structure
- Detach security policies

This is enforced structurally: the `javabin-ci-infra` role has an explicit `Deny` on `organizations:*`, and the org module has no CI workflow.

## Prerequisites

- Identity Center credentials (preferred) or IAM admin credentials
- AWS CLI v2 configured with a profile that has Organizations permissions
- Terraform >= 1.7 installed
- The state backend must already exist (run bootstrap first -- see `docs/bootstrap-runbook.md`)

## When to apply

Apply this module only after:
1. The state backend exists and is migrated to S3 (Task 0b complete)
2. The `javabin-developer-boundary` IAM policy exists in AWS (created by `terraform/platform/iam/boundary.tf`)
3. The `javabin-ci-infra` role exists (created by the platform iam module)
4. You have verified `var.exempt_roles` is correct for your account

**Order matters.** If you apply the SCP before the boundary policy and CI roles exist, the SCP will block all role creation in the account (including your own attempts to create the CI roles).

## How to apply

### Step 1: Authenticate

Using Identity Center (preferred):
```bash
aws sso login --profile javabin
```

Or configure temporary credentials in your shell.

### Step 2: Initialize

```bash
cd terraform/org
terraform init
```

### Step 3: Plan

```bash
terraform plan
```

Review the plan carefully. This module creates:
- An AWS Organization (enabling the account as the management account)
- An SCP requiring permission boundary on role creation
- An attachment of the SCP to the organization root

### Step 4: Apply

```bash
terraform apply
```

### Step 5: Verify

Check that the organization exists:
```bash
aws organizations describe-organization --profile javabin
```

Check that the SCP is attached:
```bash
aws organizations list-policies --filter SERVICE_CONTROL_POLICY --profile javabin
```

### Step 6: Commit

The code should already be committed before applying (write code, commit, then apply). If any variable adjustments were needed, commit those changes.

## Modifying the SCP

1. Edit `terraform/org/main.tf` on a branch
2. Open a PR -- CODEOWNERS requires board member approval for `terraform/org/`
3. After merge, authenticate and apply manually:
   ```bash
   cd terraform/org
   terraform plan
   terraform apply
   ```

## Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `boundary_policy_arn` | `arn:aws:iam::553637109631:policy/javabin-developer-boundary` | ARN of the boundary policy the SCP enforces |
| `exempt_roles` | `[javabin-ci-infra, root]` | Roles exempt from the boundary requirement |

Update `exempt_roles` if you add new platform-level roles that need to create roles without the boundary.

## Emergency: SCP is blocking legitimate operations

If the SCP is blocking something it should not:

1. Sign in as the management account root user (root is always exempt from SCPs)
2. Navigate to AWS Organizations > Policies > Service control policies
3. Detach or modify the SCP
4. Fix the Terraform code, commit, re-apply

Root user access should be an absolute last resort. Prefer updating `exempt_roles` and re-applying.
