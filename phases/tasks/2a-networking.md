# Task 2a — Networking Module

**Status:** Not started
**Phase:** 2 (Platform Infrastructure)
**Depends on:** 0b (state backend), 0a (discovery — need to know if VPC exists)
**Blocks:** 2b (ingress needs VPC), 2d (compute needs subnets + SGs)

## Objective

Create a fresh VPC, subnets, NAT gateway, and security groups for the Javabin platform.
Do NOT import or reference the existing default VPC. Full separation.

## Context

Read `phases/phase-2-platform.md` Task 1 for full spec.
Read `phases/discovery-results.md` for existing infra context (do not touch it).
Reference: `terraform/networking/` (copied from ai-platform-infra).

## Deliverables

### `terraform/platform/networking/`
- `main.tf` — VPC, subnets, IGW, NAT, route tables, security groups
- `variables.tf` — project, region, VPC CIDR, subnet CIDRs
- `outputs.tf` — vpc_id, public_subnet_ids, private_subnet_ids, alb_sg_id, ecs_tasks_sg_id

## Spec

| Resource | Detail |
|----------|--------|
| VPC | `10.0.0.0/16` (fresh, do NOT touch existing default VPC) |
| Public subnets | `10.0.1.0/24` (a), `10.0.2.0/24` (b) |
| Private subnets | `10.0.10.0/24` (a), `10.0.11.0/24` (b) |
| IGW | Routes public traffic |
| NAT Gateway | Single, in public-a (cost saving) |
| ALB SG | HTTPS (443) + HTTP (80) from 0.0.0.0/0, all egress |
| ECS SG | Container port from ALB SG only, all egress |

## Rules
- Create fresh VPC — do NOT import or reference existing default VPC or subnets
- Single NAT gateway is intentional (cost saving, ~$38/mo)
- Use variables for all CIDRs — never hardcode
- Naming: `javabin-{resource}` (e.g., `javabin-vpc`, `javabin-public-a`)
- AZs should use data source, not hardcoded zone names
