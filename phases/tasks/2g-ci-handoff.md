# Task 2g — Platform CI Workflow (GitHub Actions Handoff)

**Status:** Not started
**Phase:** 2 (Platform Infrastructure)
**Depends on:** 2c (IAM — needs javabin-ci-infra role)
**Blocks:** Everything after — once CI works, all platform changes go through pipeline

## Objective

Create the GitHub Actions workflow that applies Terraform for the platform repo.
This is the CI handoff point — after this works, no human runs `terraform apply`
for platform resources (except `terraform/org/` which stays manual).

## Context

Read `phases/phase-2-platform.md` Task 8 for full spec.
Reference: `.gitlab-ci.yml` (copied from ai-platform-infra — GitLab, we use GitHub Actions).

## Deliverables

### `.github/workflows/platform-ci.yml`

```yaml
name: Platform CI
on:
  push:
    branches: [main]
    paths: ['terraform/platform/**', 'terraform/lambda-src/**', 'scripts/**']
  pull_request:
    paths: ['terraform/platform/**', 'terraform/lambda-src/**', 'scripts/**']

permissions:
  id-token: write    # OIDC
  contents: read
  pull-requests: write  # PR comments

jobs:
  plan:
    # OIDC → javabin-ci-infra role
    # terraform init, validate, fmt -check, plan -detailed-exitcode
    # Upload plan + SHA256 to S3 plan artifacts bucket
    # Post plan output as PR comment (if PR)
    # Set HAS_CHANGES output

  review:
    needs: plan
    if: needs.plan.outputs.has_changes == 'true'
    # Run scripts/review-plan.py against plan output
    # Bedrock LLM review → risk level
    # Post review as PR comment (if PR) or Slack (if direct push)
    # Set RISK_LEVEL output

  apply:
    needs: [plan, review]
    if: github.ref == 'refs/heads/main' && needs.plan.outputs.has_changes == 'true'
    # Check risk level — block on HIGH, allow LOW/MEDIUM
    # Download plan from S3, verify SHA256
    # terraform apply -auto-approve plan.tfplan
    # If blocked: post to #javabin-infra-alerts with override link
```

### Drift detection (scheduled)
```yaml
  # Weekly schedule — plan only, post to Slack if drift detected
  on:
    schedule:
      - cron: '0 6 * * 1'  # Monday 06:00 UTC
```

## How OIDC Auth Works in GitHub Actions
```yaml
- uses: aws-actions/configure-aws-credentials@v4
  with:
    role-to-assume: arn:aws:iam::{account}:role/javabin-ci-infra
    aws-region: eu-west-1
    role-session-name: javabin-platform-${{ github.run_id }}
```

## Testing the Handoff
1. Create a minimal PR that changes a non-destructive resource
2. Verify: OIDC auth works, plan succeeds, review posts comment
3. Merge: verify apply runs and succeeds
4. From this point, all platform changes go through the pipeline

## Rules
- `terraform/org/` is excluded from CI paths — it stays manual
- Plan artifacts go to S3 with SHA256 — apply verifies before applying
- No-change plans skip review and apply
- HIGH risk blocks auto-apply — requires override workflow
- Drift detection runs weekly, posts to Slack
