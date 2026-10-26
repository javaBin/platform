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
| `javabin-ci-infra` | platform repo, pinned to `platform-ci.yml@refs/heads/main` | Apply platform TF (apply job only) |
| `javabin-ci-infra-plan` | platform repo, any branch / PR (`repo:javaBin/platform:*`) | Read-only plan + review + weekly drift check for platform TF |
| `javabin-ci-app-broker` | Any org repo, pinned to the platform's reusable workflows on `main` | Invoke the CI broker Lambda only |
| `javabin-ci-team-{team}` | CI broker and apply gate Lambdas (not OIDC) | Team TF plan + apply, scoped to the team by the developer boundary |
| `javabin-ci-deploy-{team}` | CI broker Lambda (not OIDC) | ECR push, ECS deploy |
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

SNS topics, EventBridge rules, Config, GuardDuty, Security Hub, account guardrails (`guardrails.tf`).

| Resource | Purpose |
|----------|---------|
| `javabin-alerts` SNS | Cost anomalies, budgets |
| `javabin-security` SNS | IAM changes, GuardDuty, Config, Security Hub |
| EventBridge rules | IAM key creation, root login, console sign-in, resource creation, security tampering (CloudTrail/Config/GuardDuty/Security Hub/KMS/guardrail disables, plus GuardDuty filters, Security Hub automation rules and control changes, Config recorder changes). `UpdateDetector`, `UpdateServiceSetting` and the filter/rule events also fire on benign changes, including platform applies |
| AWS Config | Configuration recorder (service-linked role, includes global IAM types) + S3 delivery (TLS-only, 2-year expiry) |
| Account guardrails | EBS snapshot block-public-access (block all), AMI block-public-access (block new), EBS encryption by default, SSM document public sharing off |
| IAM Access Analyzer | `javabin-external-access`, external-access findings only (free) |
| Inspector | ECR + Lambda package scanning (EC2 and Lambda code scanning off). Enabling it switches ECR registry scanning from BASIC to ENHANCED, so `scan_on_push` on repositories (`compute/`, `modules/ecr-repo`) no longer has an effect. Expect a first burst of findings into Security Hub, mostly from legacy Lambdas on deprecated runtimes |
| GuardDuty | Threat detection |
| Security Hub | Findings aggregation |
| `javabin-alert-dedup` DynamoDB | Deduplication table used by slack-alert Lambda |
| Cost allocation tags | `aws_ce_cost_allocation_tag` resources activating 7 tags: team, service, repo, environment, managed-by, created-by, commit |

### Detection tuning

`monitoring/detection-tuning.tf` holds every suppression, each with its reason in code. Review this list when a reason stops being true.

| Kind | What | Why |
|------|------|-----|
| GuardDuty filter (archive) | `Impact:IAMUser/AnomalousBehavior` from `javabin-team-provisioner` on `cognito-idp` | Scheduled Cognito group sync by the team-provisioner Lambda |
| Security Hub control disabled | CIS 1.2.0 CloudWatch.1-14, CloudTrail.5; FSBP CloudTrail.5 | No CloudTrail to CloudWatch Logs (cost). EventBridge rules and GuardDuty give the equivalent alerts |
| Security Hub control disabled | FSBP GuardDuty.7 | No EKS |
| Security Hub control disabled | FSBP Macie.1 | Not adopted (cost); revisit if PII lands in S3 |
| Security Hub control disabled | FSBP EC2.10, EC2.55-58, EC2.60 | No VPC interface endpoints (~$15/month each); traffic goes through NAT over TLS |
| Security Hub control disabled | FSBP EC2.172 | VPC block-public-access is incompatible with the public ALB and NAT |
| Security Hub control disabled | FSBP Inspector.1, Inspector.3 | Inspector EC2 and Lambda code scanning are off (legacy EB only; cost) |
| Security Hub automation rule (suppress) | RDS.2, RDS.46 on `dbmoresleep` | Intentionally public database; EC2.18/EC2.19 on its security group stay open |
| Security Hub automation rule (suppress) | ELB.18 on the platform ALB port-80 listener | Redirect-only listener |

GuardDuty filters only affect new findings; archive existing matches once in the console.

Team SSO roles' `CredentialAccess:IAMUser/AnomalousBehavior` on `ssm:GetParameter` is deliberately not filtered. The `javabin-team-{team}` permission set is `Action "*"` scoped by `StringEqualsIfExists aws:ResourceTag/team`, so untagged parameters are readable too, and this is the finding a stolen SSO session produces. Check the source country and confirm with the user before archiving one.

## lambdas

11 Lambda functions — see [lambda-functions.md](lambda-functions.md) for details.

## identity

**Not yet implemented.** Will contain IAM Identity Center + Cognito user pools.
Blocked on Google Admin access for SAML federation.
