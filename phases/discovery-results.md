# AWS Discovery Report -- Javabin Platform

**Date**: 2026-03-07
**Account**: 553637109631
**Organization**: Java brukerforeningen i Norge (javaBin), Oslo, Norway
**AWS Organizations**: Not enabled (standalone account)
**Identity Center (SSO)**: Not enabled in any region
**Authenticated as**: `arn:aws:iam::553637109631:user/alexanderamiri`

---

## 1. Primary Region

Resources are spread across **three regions**:

| Region | What Lives Here |
|--------|----------------|
| **eu-central-1** (Frankfurt) | Elastic Beanstalk apps, ECS Fargate services, ALB, RDS Postgres, CloudFormation stacks, DynamoDB, ECR, Lambda, KMS, SQS -- **primary region** |
| **eu-north-1** (Stockholm) | Coolify EC2 instance (t4g.large) -- hosts newer services |
| **eu-west-1** (Ireland) | Javazone Hubs (CloudFormation stack, Aurora Serverless Postgres, API Gateway, VPC) -- legacy |
| **us-east-1** (N. Virginia) | Lambda@Edge (javazone.no redirect), EKS VPC (unused), ACM certs for CloudFront |

**Recommendation**: Consolidate on **eu-north-1** for new platform work (closest to Oslo, newer instance types, lowest latency for Norwegian users). eu-central-1 is the current workhorse but mostly for legacy Elastic Beanstalk apps.

---

## 2. Compute Resources

### EC2 Instances

| Instance | Type | Region/AZ | State | Purpose |
|----------|------|-----------|-------|---------|
| `i-0b4ce20e8bc71a4d7` | t4g.large | eu-north-1c | running | **Coolify** (self-hosted PaaS), 150GB gp3, IP: 13.53.169.151 |
| `i-0113dc96c34262ff4` | t3a.micro | eu-central-1b | running | cakeredux-java11-prod (Elastic Beanstalk) |
| `i-0a015b99d9f12a4d8` | t2.micro | eu-central-1b | running | submitthethird-prod-env (Elastic Beanstalk) |

### Elastic Beanstalk (eu-central-1)

| Environment | Application | Health | Platform | CNAME |
|-------------|-------------|--------|----------|-------|
| cakeredux-java11-prod | cakeredux | Green | Corretto 11 / AL2 | cakeredux-java11-prod.eu-central-1.elasticbeanstalk.com |
| submitthethird-prod-env | submitthethird | Green | Java 8 / AL | submitthethird.eu-central-1.elasticbeanstalk.com |
| Moresleep-prod-11 | moresleep | **Grey (Suspended)** | Corretto 11 / AL2 | moresleep-prod-11.eu-central-1.elasticbeanstalk.com |

**EB apps with no running environments** (orphaned): switchtwo, switchredux, mooseheadreborn, moosehead

### ECS Fargate (eu-central-1)

| Cluster | Service | Running | Desired |
|---------|---------|---------|---------|
| vaultwarden | vaultwardenstack-Service* | 1 | 1 |
| moresleep-test | moresleepstack-Service* | 2 | 2 |
| cakeredux | CakeReduxStack-Service* | 1 | 1 |
| dennis-test | IaCTestingStack-Service* | **0** | **0** |

All deployed via **CDK** (CloudFormation stacks).

### AWS Amplify (eu-central-1)

| App | Platform | Repository |
|-----|----------|------------|
| 2026.javazone.no | WEB (static) | https://github.com/javaBin/2026.javazone.no |
| cupcake | WEB_COMPUTE (SSR) | https://github.com/javaBin/cupcake |

---

## 3. Networking

### VPCs

| Region | VPC ID | CIDR | Default? | Purpose |
|--------|--------|------|----------|---------|
| eu-central-1 | vpc-d55e68bc | 172.31.0.0/16 | Yes | Hosts all EB, ECS, ALB, RDS |
| eu-north-1 | vpc-4717e62e | 172.31.0.0/16 | Yes | Coolify instance |
| eu-west-1 | vpc-0105c4c176e38022d | 10.0.0.0/16 | No | Javazone Hubs (public + private subnets) |
| eu-west-1 | vpc-26d01643 | 172.31.0.0/16 | Yes | Default (unused) |
| us-east-1 | vpc-08978fa4d5241a5c0 | 192.168.0.0/16 | No | eks-vpc-VPC (appears unused) |
| us-east-1 | vpc-33d65456 | 172.31.0.0/16 | Yes | Default |

### Subnets in eu-central-1 (primary)

| Subnet | AZ | Public? | Name |
|--------|----|---------|------|
| subnet-19746c70 | eu-central-1a | Yes | (default) |
| subnet-35c1f74e | eu-central-1b | Yes | (default) |
| subnet-2f3d0c65 | eu-central-1c | Yes | (default) |
| subnet-0347ca5488bd97167 | eu-central-1a | No | private-a |
| subnet-01210e22afbd300aa | eu-central-1b | No | private-b |
| subnet-0f462674c4b62bd57 | eu-central-1c | No | private-c |

### Load Balancers (eu-central-1)

| Name | Type | DNS |
|------|------|-----|
| Default-ALB | ALB (internet-facing) | Default-ALB-1235825687.eu-central-1.elb.amazonaws.com |
| awseb-AWSEB-3DRSZMTZ8AM4 | ALB (EB-managed) | awseb-AWSEB-3DRSZMTZ8AM4-1040712083.eu-central-1.elb.amazonaws.com |
| awseb-e-y-AWSEBLoa-19019VV8OYB0L | Classic ELB (EB-managed) | awseb-e-y-AWSEBLoa-19019VV8OYB0L-1057529840.eu-central-1.elb.amazonaws.com |

### Default-ALB Routing Rules (HTTPS/443)

| Priority | Host/Path | Target |
|----------|-----------|--------|
| 1 | talks.javazone.no | EC2 instance target group (port 8080) |
| 10 | path: /dennis/* | IaCTesting target group |
| 15 | sleepingpill.javazone.no | moresleep ECS target group (port 5000) |
| 16 | cakeredux.javazone.no | CakeRedux ECS target group (port 8081) |
| 42 | vault.javazone.no | Vaultwarden ECS target group (port 80) |
| default | * | fixed-response |

### Elastic IPs (eu-central-1) -- 5 allocated

All 5 EIPs are associated with ENIs (not instances directly) -- likely used by ECS Fargate tasks or EB environments.

| IP | Association |
|----|-------------|
| 18.194.140.195 | eni-097d7308aca63df51 |
| 52.28.37.169 | eni-09b75de994f9d9619 |
| 63.180.39.62 | eni-06bea826704b641e7 |
| 63.181.167.11 | eni-0485c15cf633ad85f |
| 63.182.151.29 | eni-07df2025202b2361e |

### NAT Gateways

None in any region. Fargate tasks and private subnets have no NAT path to the internet (they may be using public subnets with public IPs).

---

## 4. DNS (Route 53)

### Hosted Zones

| Zone | Records | Private |
|------|---------|---------|
| javazone.no | 78 | No |
| talk.javazone.no | 2 | No |

### DNS Routing Summary

| Target | Subdomains |
|--------|------------|
| **Coolify** (13.53.169.151, eu-north-1) | 2023, 2024, 2025, talks |
| **External** (194.63.248.52) | academy, ads, app, bergen, discord, frivillig, oslo, partnerportal, submit, trondheim, video |
| **CloudFront** distributions | javazone.no (apex), www, 2014-2020, cake, cakeredux, hubs, j-team, member, moosehead, sleepingpill, submit (CF), superdupersecretstash |
| **GitHub Pages** | 2020, 2021, 2022 (javaBin.github.io) |
| **ALB** (eu-central-1) | cakeredux, sleepingpill, vault |
| **Amplify** | 2026, cupcake |
| **Auth0** | login.javazone.no (Auth0 tenant: dev-x84wvhy7jcdp58vb) |
| **Hubs ELB** (eu-west-1) | hubs |

---

## 5. Storage

### S3 Buckets (45 total)

**Active/Important:**
| Bucket | Created | Purpose |
|--------|---------|---------|
| javazone-terraform-state | 2025-10-25 | Terraform state (versioning enabled) |
| javabin-vaultwarden-data | 2025-10-25 | Vaultwarden persistent data |
| 2026.javazone.no | 2025-11-11 | Amplify site |
| javazone.no | 2025-11-11 | Main site redirect |
| www.javazone.no | 2025-11-11 | WWW redirect |
| javazone-assets | 2019-11-01 | Shared assets (CloudFront origin) |
| javazone-backup | 2016-04-01 | Backups |
| javazone-backup-confluence | 2017-10-03 | Confluence backups |
| javazone-video | 2016-04-23 | Video storage |
| cake-frontend-prod | 2018-01-20 | Cake frontend |

**Historical conference sites** (S3 static hosting): 2002-2016.javazone.no (15 buckets)

**Elastic Beanstalk**: elasticbeanstalk-eu-central-1-553637109631, elasticbeanstalk-eu-west-1-553637109631

**CDK**: cdk-hnb659fds-assets-553637109631-eu-central-1

**Javazone Hubs** (Mozilla Hubs fork, likely dormant): 5 buckets (assets, box-keys, link-redirector, root-redirector, speelycaptor)

**Datomic**: datomic-code-*, datomic-moresleep-test-*

**CloudFormation templates**: cf-templates-* (2 buckets)

### Terraform State

One state file: `sqs-infrastructure/terraform.tfstate`
DynamoDB lock table: `terraform-lock` (eu-central-1)

---

## 6. Databases

### RDS

| Instance | Engine | Class | Region | Status |
|----------|--------|-------|--------|--------|
| dbmoresleep | PostgreSQL | db.t4g.micro | eu-central-1a | available |
| javazone-hubs-app-db (Aurora cluster) | aurora-postgresql | db.serverless | eu-west-1a | available |

### DynamoDB (eu-central-1)

| Table | Purpose |
|-------|---------|
| terraform-lock | Terraform state locking |
| datomic-datomic-moresleep-test | Datomic database (moresleep) |
| datomic-datomic-moresleep-test-catalog | Datomic catalog |

### SQS (eu-central-1)

| Queue | Purpose |
|-------|---------|
| javazone-talk-events | Talk event processing |
| javazone-talk-events-dlq | Dead letter queue |

---

## 7. Certificates (ACM)

| Region | Domain | Status | Type |
|--------|--------|--------|------|
| eu-central-1 | *.javazone.no | **ISSUED** | Amazon-issued |
| eu-central-1 | switch.javazone.no | EXPIRED | Amazon-issued |
| us-east-1 | javazone.no | **ISSUED** | Amazon-issued (for CloudFront) |
| us-east-1 | hubs.javazone.no | **ISSUED** | Amazon-issued |
| us-east-1 | *.javazone.no | EXPIRED | Imported |
| us-east-1 | javazone.link | EXPIRED | Amazon-issued |
| us-east-1 | *.javazone-hubs.com | EXPIRED | Amazon-issued |
| eu-west-1 | *.javazone-hubs.com | EXPIRED | Amazon-issued |
| eu-west-1 | hubs.javazone.no | EXPIRED | Amazon-issued |

---

## 8. CloudFront (25 distributions)

Most are for historical conference sites. Active ones:

| Alias | Origin | Notes |
|-------|--------|-------|
| javazone.no | www.javazone.no S3 | Apex domain redirect |
| hubs.javazone.no | S3 + ELB | Mozilla Hubs fork (likely dormant) |
| talks.javazone.no | submitthethird EB | Talk submissions |
| cakeredux.javazone.no | cakeredux EB | Cake Redux app |
| sleepingpill.javazone.no | moresleep EB | Sleeping Pill API |
| submit.javazone.no | submit EB | Submit frontend |
| member.javazone.no | serenity EB | Member management |
| 2019-2020.javazone.no | External origins (java.no) | Past conference sites |

---

## 9. Lambda Functions

| Function | Runtime | Region | Last Modified | Notes |
|----------|---------|--------|---------------|-------|
| AutomaticBlogPoster | **python2.7** | eu-central-1 | 2018-05-15 | DEPRECATED RUNTIME |
| javazone26-amplify-notify-slack | nodejs22.x | eu-central-1 | 2026-01-17 | Amplify build notifications |
| serverlessrepo-javazone-no-cloudfronthttpredirect-* | **nodejs6.10** | eu-central-1 | 2019-01-15 | DEPRECATED RUNTIME |
| vaultwardenstack-CustomS3AutoDeleteObjects* | nodejs22.x | eu-central-1 | 2025-10-25 | CDK custom resource |
| javazone-no-redirect | **nodejs6.10** | us-east-1 | 2019-01-15 | Lambda@Edge, DEPRECATED RUNTIME |

---

## 10. IAM

### Users (26 total)

**Recently active** (last 30 days): alexander.samsig, alexanderamiri, anderska, chris, dennis, stian.mikal.nygaard

**Stale users** (not logged in >1 year): beiske, bendiksolheim, erikd, espenhh, gareth, hamnis, henrikno, ingara, rasmantuta, sdalatun, stiglau. Service accounts: javazone-backup, javazone-hubs-send-email.

### Roles (notable)

| Role | Purpose |
|------|---------|
| GithubOIDC-Role-WueNBzW8J9FT | GitHub Actions OIDC (8 repos: cupcake, IaC-testing, moresleep, cake-redux, elasticsearch-javazone, webhook-receiver, es-indexer-worker, spacecake) |
| CakeReduxStack-TaskDef* | ECS task roles for CakeRedux |
| moresleepstack-TaskDef* | ECS task roles for moresleep |
| vaultwardenstack-TaskDef* | ECS task roles for Vaultwarden |
| IaCTestingStack-TaskDef* | ECS task roles for IaC testing |
| cdk-hnb659fds-* | CDK bootstrap roles |
| aws-elasticbeanstalk-* | EB service/instance roles |
| javazone-hubs-* | 12 roles for Mozilla Hubs fork |
| Whydah_* | 3 legacy roles (2014) |
| javabinAktiv | Legacy role (2016) |

### GitHub OIDC Provider

Configured at `token.actions.githubusercontent.com`, trusted repos:
- javaBin/cupcake, javaBin/IaC-testing, javaBin/moresleep, javaBin/cake-redux
- javaBin/elasticsearch-javazone, javaBin/webhook-receiver, javaBin/es-indexer-worker, javaBin/spacecake

### Customer-Managed Policies (8)

- sops-kms-policy
- Lists3
- datomic-admin-datomic-moresleep-test-eu-central-1
- datomic-code-eu-central-1-F7F305E7
- javazone-hubs-BasePolicy-1FNEOJW92SLSC
- 3 Lambda execution role policies

---

## 11. KMS Keys

| Alias | Key ID | Purpose |
|-------|--------|---------|
| alias/datomic | 1b1c9f47-... | Datomic encryption |
| alias/demo-kms-sops-key | a6a46dc7-... | SOPS secret encryption |
| alias/aws/acm | aef85c27-... | ACM managed |
| alias/aws/lambda | 1d1f6643-... | Lambda managed |
| alias/aws/rds | 785d8d39-... | RDS managed |
| alias/aws/s3 | 8696bba5-... | S3 managed |

---

## 12. Security Services

| Service | Status |
|---------|--------|
| **GuardDuty** | NOT ENABLED (any region) |
| **Security Hub** | NOT ENABLED (any region) |
| **AWS Config** | NOT ENABLED (any region) |
| **CloudTrail** | NOT ENABLED (no trails found) |
| **AWS Organizations** | NOT ENABLED |
| **Identity Center (SSO)** | NOT ENABLED |

**This is a significant security gap.** No audit trail, no threat detection, no compliance monitoring.

---

## 13. API Gateway (eu-west-1)

| API | Purpose |
|-----|---------|
| speelycaptor | Javazone Hubs component |
| nearspark | Javazone Hubs component |
| photomnemonic | Javazone Hubs component |

All part of the Javazone Hubs deployment (likely dormant).

---

## 14. CloudFormation Stacks

### eu-central-1 (12 stacks)

| Stack | Status | Purpose |
|-------|--------|---------|
| CDKToolkit | CREATE_COMPLETE | CDK bootstrap |
| InfrastructureAwsStack | UPDATE_COMPLETE | Shared infra (ALB, VPC config) |
| Ec2Stack | UPDATE_COMPLETE | EC2 instance for submitthethird/talks |
| KmsStack | UPDATE_COMPLETE | KMS keys |
| IaCTestingStack | UPDATE_COMPLETE | Testing ECS service (0 tasks) |
| moresleepstack | UPDATE_COMPLETE | Moresleep ECS Fargate |
| CakeReduxStack | UPDATE_COMPLETE | CakeRedux ECS Fargate |
| vaultwardenstack | UPDATE_COMPLETE | Vaultwarden ECS Fargate |
| GithubOIDC | UPDATE_ROLLBACK_COMPLETE | GitHub OIDC (rollback state) |
| awseb-e-przmhdmxz8-stack | UPDATE_COMPLETE | EB environment (cakeredux) |
| awseb-e-ydpjcu2np5-stack | UPDATE_COMPLETE | EB environment (submitthethird) |
| serverlessrepo-javazone-no-http-redirect | CREATE_COMPLETE | CloudFront HTTP redirect |

### eu-west-1 (6 stacks)

All related to **Javazone Hubs** (Mozilla Hubs fork from 2020).

---

## 15. Cost Analysis

### February 2026 Full Month: $337.70

| Service | Monthly Cost | Notes |
|---------|-------------|-------|
| EC2 Compute | $81.81 | Coolify t4g.large + 2 EB instances |
| Tax | $67.54 | Norwegian VAT |
| Elastic Load Balancing | $56.60 | 2 ALBs + 1 Classic ELB (3 total!) |
| VPC | $51.32 | 5 Elastic IPs ($3.65/mo each unused = ~$18/mo) + data transfer |
| ECS Fargate | $39.78 | 4 services (vaultwarden, moresleep, cakeredux, IaC-testing) |
| RDS | $17.74 | db.t4g.micro (eu-central-1) + Aurora Serverless (eu-west-1) |
| EC2 Other | $14.55 | EBS volumes, snapshots |
| KMS | $3.00 | 6 keys |
| S3 | $2.14 | 45 buckets |
| Route 53 | $1.25 | 2 hosted zones + queries |
| Amplify | $0.91 | 2 apps |
| CloudWatch | $0.50 | Logs and metrics |
| Secrets Manager | $0.40 | |
| ECR | $0.12 | 1 repo |
| Backup | $0.05 | |

### Cost Observations

- **$56.60/mo on load balancers** is the second-highest cost. Three LBs is excessive for this workload.
- **$51.32/mo on VPC** -- Elastic IPs and data transfer. The 5 EIPs cost ~$18/mo if any are idle.
- **Moresleep EB environment is suspended** but the RDS and Datomic resources are still running.
- **Aurora Serverless in eu-west-1** (Javazone Hubs) is likely dormant but still incurring charges.
- **Projected annual cost**: ~$4,050/year (pre-tax ~$3,240).

---

## 16. Bedrock Availability

Claude models are available in both **eu-west-1** and **eu-central-1**:

| Model | eu-west-1 | eu-central-1 |
|-------|-----------|--------------|
| claude-opus-4-6 | Yes | Yes |
| claude-opus-4-5 | Yes | Yes |
| claude-sonnet-4-6 | Yes | Yes |
| claude-sonnet-4-5 | Yes | Yes |
| claude-sonnet-4 | Yes | Yes |
| claude-haiku-4-5 | Yes | Yes |
| claude-3-7-sonnet | Yes | Yes |
| claude-3-5-sonnet | Yes | Yes |
| claude-3-sonnet | Yes (3 variants) | No |
| claude-3-haiku | Yes (2 variants) | Yes |

**Note**: Model access has not been requested/enabled yet (only listing available models, not enabled ones). You will need to request model access in the Bedrock console before using them.

---

## 17. Recommendations

### Import into Terraform (existing resources to manage)

| Resource | Priority | Notes |
|----------|----------|-------|
| Route 53 hosted zones + records | HIGH | 78 records in javazone.no zone |
| Default-ALB + listener rules + target groups | HIGH | Core routing for ECS services |
| ACM certificates (issued ones) | HIGH | *.javazone.no (eu-central-1), javazone.no (us-east-1) |
| VPC + subnets (eu-central-1) | MEDIUM | Default VPC, but has custom private subnets |
| Security groups | MEDIUM | 13 security groups in eu-central-1 |
| S3 buckets (active ones) | MEDIUM | terraform-state, vaultwarden-data, javazone-assets, javazone.no, www.javazone.no |
| DynamoDB terraform-lock table | MEDIUM | Already used for TF state locking |
| SQS queues | MEDIUM | javazone-talk-events + DLQ |
| KMS keys (sops, datomic) | LOW | Custom keys |
| GitHub OIDC provider + role | LOW | Already exists, UPDATE_ROLLBACK_COMPLETE stack |
| Elastic IPs | LOW | 5 EIPs, verify which are actually needed |

### Create Fresh (new resources)

| Resource | Priority | Notes |
|----------|----------|-------|
| CloudTrail | CRITICAL | No audit trail exists |
| GuardDuty | HIGH | No threat detection |
| Terraform remote state (if migrating from CDK) | HIGH | State bucket exists but only has 1 state file |
| New VPC (eu-north-1) if migrating to Stockholm | MEDIUM | Currently using default VPC |
| Bedrock model access | MEDIUM | Models available but not enabled |

### Cleanup Candidates (potential cost savings)

| Resource | Estimated Savings | Risk |
|----------|------------------|------|
| Consolidate 3 LBs to 1 ALB | ~$35/mo | Medium -- need to migrate EB apps |
| Remove Moresleep EB environment (suspended) | ~$5/mo | Low -- already migrated to ECS |
| Review Aurora Serverless (eu-west-1 Hubs DB) | ~$5-10/mo | Low -- Hubs appears dormant |
| Remove expired ACM certificates | $0 | None |
| Delete orphaned EB applications (4 apps, 0 envs) | $0 | None |
| Review 5 Elastic IPs | Up to $18/mo | Check if all are needed |
| Delete EKS VPC in us-east-1 | $0 | None -- no EKS clusters exist |
| Retire Lambda functions on deprecated runtimes | $0 | Low -- python2.7, nodejs6.10 will stop working |

### Concerns and Surprises

1. **No security services at all.** No CloudTrail, GuardDuty, Security Hub, or Config. This means no audit trail of API calls, no threat detection, and no compliance monitoring. This should be the first thing addressed.

2. **GithubOIDC stack is in UPDATE_ROLLBACK_COMPLETE state.** The OIDC provider itself works (it exists), but the CloudFormation stack is in a failed state. The role trust policy is functional but the stack cannot be updated.

3. **Three load balancers for a small workload.** Default-ALB (CDK-managed), an EB-managed ALB, and an EB-managed Classic ELB. This accounts for ~$57/mo.

4. **Mixed deployment strategies.** The account uses Elastic Beanstalk, ECS Fargate (via CDK), Amplify, Coolify (self-hosted on EC2), and GitHub Pages. This is a lot of operational surface area for a volunteer-run organization.

5. **26 IAM users, many stale.** Only 6 users have logged in within the last 30 days. 11 users have not logged in for over a year. No MFA enforcement visible. No SSO/Identity Center.

6. **Coolify instance hosts multiple services.** The 13.53.169.151 IP serves talks.javazone.no, 2023-2025.javazone.no. This is a single point of failure with no auto-scaling or redundancy.

7. **194.63.248.52 is an external IP** (not AWS). 11 subdomains point to it (academy, ads, app, bergen, discord, frivillig, oslo, partnerportal, submit, trondheim, video). This appears to be a separate hosting provider.

8. **login.javazone.no points to Auth0** (tenant: dev-x84wvhy7jcdp58vb). This is the identity provider for the ecosystem.

9. **Lambda functions on deprecated runtimes.** Three functions use python2.7 or nodejs6.10, which are past end-of-life. AWS may block invocations at any time.

10. **Javazone Hubs infrastructure (eu-west-1)** appears mostly dormant but is still incurring costs (Aurora Serverless, CloudFront, API Gateways). 6 CloudFormation stacks, 12 IAM roles, 5 S3 buckets, and a dedicated VPC remain provisioned.
