# Task 2d — Compute Module (ECS Cluster)

**Status:** Not started
**Phase:** 2 (Platform Infrastructure)
**Depends on:** 2a (networking — needs VPC)
**Blocks:** Phase 4 (app onboarding needs ECS cluster)

## Objective

Create the ECS Fargate cluster and ECR repositories for CI images.

## Context

Read `phases/phase-2-platform.md` Task 4 for full spec.
Reference: `terraform/compute/` (copied from ai-platform-infra).

## Deliverables

### `terraform/platform/compute/`
- `main.tf` — ECS cluster, ECR repos for CI images
- `variables.tf` — project, region
- `outputs.tf` — ecs_cluster_name, ecs_cluster_arn

## Spec

| Resource | Detail |
|----------|--------|
| ECS Cluster | `javabin-platform`, Fargate, Container Insights enabled |
| ECR: jvm-ci | CI image for Maven/Kotlin builds |
| ECR: ts-ci | CI image for TypeScript/pnpm builds |
| ECR: platform-ci | CI image for Terraform plan/apply jobs |

## Rules
- Cluster name: `javabin-platform`
- Container Insights: enabled
- ECR repos get lifecycle policies (from reference ecr-repo module pattern)
- CI images are rebuilt monthly (Phase 3 handles the workflow)
