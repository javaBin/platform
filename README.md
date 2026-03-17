# Javabin Platform

Terraform-managed AWS infrastructure for [javaBin](https://github.com/javaBin), the Norwegian Java User Group.

## What This Repo Does

This is the central infrastructure repo. It provisions shared AWS resources (VPC, ALB, ECS cluster, IAM, monitoring) and provides reusable Terraform modules and GitHub Actions workflows that app repos consume.

## CI Pipeline

```
Push to main → terraform plan → LLM risk review → terraform apply
```

- **No-change plans** skip review and apply automatically
- **HIGH risk** changes block auto-apply and notify Slack; a board member can override
- Plan artifacts are stored in S3, SHA-verified before apply

See [docs/ci-workflow.md](docs/ci-workflow.md) for details.

## Repository Structure

```
terraform/
  platform/       7 sub-modules (networking, ingress, iam, compute, monitoring, lambdas, identity)
  modules/        12 reusable modules for app repos
  lambda-src/     Python handlers for 6 Lambda functions
  org/            AWS Organizations (human-applied, no CI)
  state/          State backend bootstrap config
scripts/          Bootstrap, code generation, plan review, block notification
docs/             Architecture docs, runbooks, references
.github/workflows/ 13 workflows (platform CI + reusable app workflows)
```

## Platform Sub-Modules

| Module | Purpose |
|--------|---------|
| `networking` | VPC, subnets, NAT gateway, security groups |
| `ingress` | ALB, ACM certificates, Route53 DNS |
| `iam` | GitHub OIDC provider, CI roles, permission boundary |
| `compute` | ECS Fargate cluster, ECR base config |
| `monitoring` | SNS topics, EventBridge rules, Config, GuardDuty, Security Hub |
| `lambdas` | Slack alerts, cost reports, compliance, override cleanup, team provisioner |
| `identity` | IAM Identity Center (SAML + Google), Cognito user pools, 2FA |

## How App Repos Use the Platform

1. **Register** your team in [javaBin/registry](https://github.com/javaBin/registry)
2. **Add `app.yaml`** to your repo root ([reference](docs/app-yaml-reference.md))
3. **Call the reusable workflow** from your repo's CI:
   ```yaml
   jobs:
     platform:
       uses: javaBin/platform/.github/workflows/javabin.yml@main
   ```
4. The workflow detects your repo contents, builds, plans infra, reviews, and deploys

## Reusable Modules

App repos source these via `git::` URLs:

| Module | What |
|--------|------|
| `app-stack` | Golden path — reads `app.yaml`, creates all infra |
| `ecr-repo` | ECR repository with lifecycle policy |
| `ecs-service` | ECS Fargate service definition |
| `service-routing` | ALB target group + listener rule + DNS |
| `service-role` | ECS task IAM role with composable policies |
| `service-bucket` | S3 bucket with IAM policy output |
| `service-database` | DynamoDB table with IAM policy output |
| `service-secret` | SSM Parameter Store SecureString with IAM policy output |
| `service-queue` | SQS queue + DLQ with IAM policy output |
| `service-alarm` | CloudWatch alarms for ECS service |
| `platform-data` | Read-only data sources for shared infra |
| `cognito-app-client` | Cognito app client registration |

## Documentation

- [Platform modules](docs/platform-modules.md) — sub-module architecture
- [Lambda functions](docs/lambda-functions.md) — triggers, SSM params, env vars
- [CI workflow](docs/ci-workflow.md) — plan, review, apply pipeline
- [Reusable modules](docs/reusable-modules.md) — app module inputs/outputs
- [Reusable workflows](docs/reusable-workflows.md) — javabin.yml orchestration
- [app.yaml reference](docs/app-yaml-reference.md) — service configuration schema
- [Bootstrap runbook](docs/bootstrap-runbook.md) — state backend setup
- [Org runbook](docs/org-runbook.md) — AWS Organizations setup

## Related Repos

- [javaBin/registry](https://github.com/javaBin/registry) — team registration
- [javaBin/app-template](https://github.com/javaBin/app-template) — starter template for new services
- [javaBin/javabin-cli](https://github.com/javaBin/javabin-cli) — developer CLI
- [javaBin/docs](https://github.com/javaBin/docs) — organization-wide documentation
