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
| SGs | `javabin-alb-sg` (80/443 inbound), `javabin-ecs-tasks-sg` (ALB-only inbound) |

**Outputs:** `vpc_id`, `public_subnet_ids`, `private_subnet_ids`, `alb_sg_id`, `ecs_tasks_sg_id`

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

### Detection tuning

`monitoring/detection-tuning.tf` holds every suppression, each with its reason in code. Review this list when a reason stops being true.

| Kind | What | Why |
|------|------|-----|
| GuardDuty filter (archive) | `Impact:IAMUser/AnomalousBehavior` from `javabin-team-provisioner` on `cognito-idp` | Scheduled Cognito group sync by the team-provisioner Lambda |
| GuardDuty filter (archive) | `CredentialAccess:IAMUser/AnomalousBehavior`, API `GetParameter`, from `AWSReservedSSO_javabin-team-*` roles | The team permission set only allows SSM reads under `/javabin/apps/<own team>/*`. Role names come from a data source; new teams are added on the next apply |
| Security Hub control disabled | CIS 1.2.0 CloudWatch.1-14, CloudTrail.5; FSBP CloudTrail.5 | No CloudTrail to CloudWatch Logs (cost). EventBridge rules and GuardDuty give the equivalent alerts |
| Security Hub control disabled | FSBP GuardDuty.7 | No EKS |
| Security Hub control disabled | FSBP Macie.1 | Not adopted (cost); revisit if PII lands in S3 |
| Security Hub control disabled | FSBP EC2.10, EC2.55-58, EC2.60 | No VPC interface endpoints (~$15/month each); traffic goes through NAT over TLS |
| Security Hub control disabled | FSBP EC2.172 | VPC block-public-access is incompatible with the public ALB and NAT |
| Security Hub control disabled | FSBP Inspector.1, Inspector.3 | Inspector EC2 and Lambda code scanning are off (legacy EB only; cost) |
| Security Hub automation rule (suppress) | RDS.2, RDS.46 on `dbmoresleep` | Intentionally public database; EC2.18/EC2.19 on its security group stay open |
| Security Hub automation rule (suppress) | ELB.18 on the platform ALB port-80 listener | Redirect-only listener |

GuardDuty filters only affect new findings; archive existing matches once in the console.

## lambdas

11 Lambda functions — see [lambda-functions.md](lambda-functions.md) for details.

## identity

**Not yet implemented.** Will contain IAM Identity Center + Cognito user pools.
Blocked on Google Admin access for SAML federation.
