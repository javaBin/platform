# Task 0a — AWS Discovery

**Status:** In progress
**Phase:** 0 (Foundation)
**Depends on:** Nothing — this is the first task
**Blocks:** All other tasks (results determine region, existing resources, imports)

## Objective

Run read-only discovery against the existing Javabin AWS account. Document every existing
resource. This report drives all subsequent Terraform decisions.

## AWS Profile

Use `--profile javabin` on every AWS CLI command.

## Discovery Checklist

Run these commands and record output for each:

### Account & Org
- `sts get-caller-identity` — account ID, current user
- `organizations describe-organization` — is Organizations enabled?

### Region Detection
Check eu-west-1, eu-north-1, eu-central-1, us-east-1 for EC2 instances to find where infra lives.

### Networking
- VPCs (all regions) — ID, CIDR, default flag, name
- Subnets — ID, VPC, AZ, CIDR, public flag
- NAT Gateways, Internet Gateways
- Security Groups (non-default)

### IAM
- Users (name, created, last used)
- Roles (non-AWS-managed)
- Local policies
- Instance profiles
- Identity Center instances

### Storage & Data
- S3 buckets
- DynamoDB tables (all regions)
- RDS instances
- ECR repositories

### Compute
- EC2 instances (find Coolify + Vaultwarden)
- ECS clusters
- Elastic Beanstalk environments (cake-redux, moresleep expected)
- Lambda functions

### DNS & Certs
- Route53 hosted zones (java.no must exist)
- ACM certificates (all regions)

### Security Services
- GuardDuty detectors
- Security Hub status
- AWS Config recorders
- CloudTrail trails

### Infrastructure as Code
- CloudFormation stacks (from CDK or EB)

### Cost
- Last 30 days by service
- Baseline monthly spend

### Bedrock
- Model availability in eu-west-1 and eu-central-1
- Claude model IDs available

## Output

Write comprehensive report to `phases/discovery-results.md` with:
1. Account ID and primary region
2. All resources organized by service
3. Security service status
4. Current spend
5. Bedrock availability
6. What to import vs create fresh
7. Surprises or concerns

## Rules
- **READ ONLY.** Do not create, modify, or delete anything.
- Record even "empty" results — knowing something doesn't exist is valuable.
- If a command fails, record the error — it tells us about permissions or service state.
