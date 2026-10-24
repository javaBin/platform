# Platform Terraform Modules

Sub-modules under `terraform/platform/` — applied via CI (push to main).

## Module Architecture

```
main.tf (root) wires these together:
  networking ─► ingress
  networking ─► compute
  iam (standalone)
  monitoring ─► lambdas
  identity (not yet implemented)
```

## networking

VPC, subnets, NAT gateway, security groups.

| Resource | Name Pattern |
|----------|-------------|
| VPC | `javabin-vpc` (10.0.0.0/16) |
| Public subnets | 3 AZs, tagged `tier=public` |
| Private subnets | 3 AZs, tagged `tier=private` |
| NAT Gateway | Single NAT in first public subnet |
| SGs | `javabin-alb-sg` (80/443 inbound), `javabin-ecs-tasks-sg` (ALB-only inbound; legacy, apps now get their own `{team}-{app}-tasks` group from `app-network`) |
| Cloud Map namespace | `javabin.internal` (private DNS, tagged `team=shared`) — app-to-app names `{team}-{app}.javabin.internal` |

**Outputs:** `vpc_id`, `vpc_arn`, `public_subnet_ids`, `private_subnet_ids`, `alb_sg_id`, `ecs_tasks_sg_id`, `service_discovery_namespace_{id,arn,name}`

## ingress

ALB, ACM wildcard certificate, Route53 DNS.

| Resource | Name Pattern |
|----------|-------------|
| ALB | `javabin-alb` (public subnets) |
| ACM certs | `*.javazone.no` (default) plus `*.java.no` and `*.javabin.no` attached by SNI (DNS validation) |
| HTTPS listener | Port 443, default action = fixed 404 |
| Route53 | Validation records for ACM |

**Inputs:** `public_subnet_ids`, `alb_sg_id` from networking; `extra_domains` (domain → zone ID) from dns
**Outputs:** `alb_arn`, `https_listener_arn`, `alb_dns_name`, `alb_zone_id`

## iam

GitHub OIDC provider (data source), CI roles, permission boundary, ECS execution role.

| Role | Trust | Purpose |
|------|-------|---------|
| `javabin-ci-infra` | platform repo, main branch | Apply platform TF |
| `javabin-ci-infra-plan` | platform repo, main branch + PRs | Read-only plan + review for platform TF |
| `javabin-ci-app-{repo}` | Per-app, pinned to `tf-plan.yml` | App TF plan + apply |
| `javabin-ci-deploy-{repo}` | Per-app, pinned to `tf-apply.yml` | ECR push, ECS deploy |
| `javabin-ci-override-approver` | Board members, pinned to `approve-override.yml` | SSM override tokens |
| `javabin-ci-registry` | Registry repo | Team provisioning dispatch |
| `javabin-ecs-execution` | ECS tasks | Pull images, write logs, read secrets |

**Key design:** `job_workflow_ref` condition prevents app repos from writing rogue workflows.

## compute

ECS Fargate cluster + CI ECR repositories.

| Resource | Name |
|----------|------|
| ECS cluster | `javabin-platform` |
| ECR repos | `javabin-ci-jvm`, `javabin-ci-ts`, `javabin-ci-platform` |

## monitoring

SNS topics, EventBridge rules, Config, GuardDuty, Security Hub.

| Resource | Purpose |
|----------|---------|
| `javabin-alerts` SNS | Cost anomalies, budgets |
| `javabin-security` SNS | IAM changes, GuardDuty, Config, Security Hub |
| EventBridge rules | IAM key creation, root login, console sign-in, resource creation |
| AWS Config | Configuration recorder + S3 delivery |
| GuardDuty | Threat detection |
| Security Hub | Findings aggregation |
| `javabin-alert-dedup` DynamoDB | Deduplication table used by slack-alert Lambda |
| Cost allocation tags | `aws_ce_cost_allocation_tag` resources activating 7 tags: team, service, repo, environment, managed-by, created-by, commit |

## lambdas

11 Lambda functions — see [lambda-functions.md](lambda-functions.md) for details.

## identity

**Not yet implemented.** Will contain IAM Identity Center + Cognito user pools.
Blocked on Google Admin access for SAML federation.
