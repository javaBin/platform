# Task 2e — Monitoring Module

**Status:** Not started
**Phase:** 2 (Platform Infrastructure)
**Depends on:** 0b (state backend)
**Blocks:** 2f (lambdas need SNS topic ARNs)

## Objective

Create the monitoring infrastructure: SNS topics, EventBridge rules, AWS Config,
Cost Anomaly Detection, GuardDuty, and Security Hub.

## Context

Read `phases/phase-2-platform.md` Task 5 for full spec.
Reference: `terraform/monitoring/` (copied from ai-platform-infra).

**New vs reference:** Add Security Hub (not in reference). GuardDuty findings go through
Security Hub before alerting, which normalizes and deduplicates.

## Deliverables

### `terraform/platform/monitoring/`
- `main.tf` — All monitoring resources
- `variables.tf` — project, region, account_id
- `outputs.tf` — alerts_topic_arn, security_topic_arn

## Spec

### SNS Topics
- `javabin-alerts` — cost anomalies, budgets
- `javabin-security` — security events (IAM changes, GuardDuty, Config, Security Hub)

### EventBridge Rules → javabin-security SNS
1. IAM changes (CreateRole, DeleteRole, PutRolePolicy, AttachRolePolicy, etc.)
2. Console logins
3. Config compliance (non-compliant resources)
4. Resource creation (EC2, S3, RDS, ECS, ELB, Lambda, SQS, DynamoDB)
5. Resource modification/deletion

### AWS Config
- Recorder on all resources
- S3 bucket: `javabin-config-{account_id}` for Config logs
- Config Rule: `javabin-required-tags` — checks for `project`, `team`, `managed-by`

### Cost Anomaly Detection
- Monitor type: DIMENSIONAL (by SERVICE)
- Threshold: $10 absolute spike
- Notification: javabin-alerts SNS, IMMEDIATE frequency

### GuardDuty
- Enable threat detection
- ECS Runtime Monitoring (Fargate agent auto-managed)
- Findings → Security Hub (NOT direct to SNS)

### Security Hub (NEW)
- Enable Security Hub
- Connect GuardDuty findings
- Connect Config findings
- Security Hub → EventBridge → javabin-security SNS → slack-alert Lambda

## Rules
- Check discovery results first — if GuardDuty/Config already enabled, import
- Security Hub normalizes findings before alerting — reduces noise
- Config rule checks for three tags: project, team, managed-by
- Cost threshold in USD ($10) — NOK conversion happens in the Lambda
