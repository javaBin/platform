# Task 3a — Reusable Terraform Modules

**Status:** Not started
**Phase:** 3 (Golden Path)
**Depends on:** 2a-2d (platform infra must exist for data sources)
**Blocks:** 3c (app-stack module needs all sub-modules), Phase 4 (apps use modules)

## Objective

Adapt the 11 reusable Terraform modules from the reference implementation for Javabin.
Add the new `cognito-app-client` module.

## Context

Read `phases/phase-3-golden-path.md` Task 4 for full spec.
Reference modules: `terraform/modules/` (copied from ai-platform-infra).

**Key changes from reference:**
- Naming: `ai-platform-` → project variable
- Domain: `cxense.com` → `java.no`
- Region: configurable (not hardcoded eu-central-1)
- New: `cognito-app-client` module

## Deliverables

### Modules under `modules/`

| Module | What | Key Changes |
|--------|------|-------------|
| `platform-data/` | Discover shared infra (VPC, ALB, ECS, Route53) | Update naming patterns, domain |
| `ecr-repo/` | ECR + lifecycle policy | Update lifecycle rules per PLAN.md |
| `service-routing/` | ALB target group + listener rule + DNS | Update domain, health check defaults |
| `service-role/` | ECS task IAM role + composable policies | Update naming |
| `ecs-service/` | ECS Fargate service (task def, service, log group) | Update naming, add env convention |
| `service-bucket/` | S3 bucket + IAM policy output | Update naming pattern |
| `service-database/` | DynamoDB table + IAM policy output | Keep as-is, update naming |
| `service-secret/` | Secrets Manager secret + IAM policy output | Update path prefix |
| `service-queue/` | SQS queue + DLQ + IAM policy output | Keep as-is, update naming |
| `service-alarm/` | CloudWatch alarms for ECS | Keep as-is, update naming |
| `app-stack/` | Golden path — reads app.yaml, calls all above | Major rewrite for new schema |
| `cognito-app-client/` | NEW: Cognito app client registration | Create from scratch |

### ECR Lifecycle Policy (standardized)
```json
[
  {"rulePriority": 1, "Keep last 5 semver releases", "tagPrefixList": ["v"], "countNumber": 5},
  {"rulePriority": 2, "Keep last 3 main builds", "tagPrefixList": ["main-","master-"], "countNumber": 3},
  {"rulePriority": 3, "Expire untagged after 1 day"},
  {"rulePriority": 4, "Expire other tags after 7 days"}
]
```

### `cognito-app-client/` (NEW)
```hcl
# Creates Cognito app client + stores credentials in SSM
variable "app_name" {}
variable "pool" {}  # "internal" | "external" | "both"
# Outputs: client_id, ssm_parameter_arns
```

## Rules
- App repos source modules via `git::https://github.com/javaBin/platform.git//modules/{name}`
- All modules must work standalone (no cross-module dependencies except platform-data)
- platform-data discovers by naming convention — update patterns for `javabin-*`
- Each module has main.tf, variables.tf, outputs.tf
- app-stack reads `app.yaml` not `.piano/app.yaml`
