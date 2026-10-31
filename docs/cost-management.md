# Cost Management

How the platform watches AWS spend, and where each piece lives. All of it is
platform Terraform or a platform Lambda; nothing here is configured by hand.

Steady state (2026-10): about **$550/month including 25% VAT** (~$440 before
tax), flat since April 2026. Most of it is fixed cost: load balancers, public
IPv4 addresses, the NAT gateway, AWS Config, Security Hub and the legacy
pre-platform stacks. Budgets and Cost Explorer report cost **with tax**.

## Alerts

All cost alerts go to the `javabin-alerts` SNS topic, which the `slack-alert`
Lambda posts to **#javabin-cost-alerts**.

| Signal | Where | Fires when |
|---|---|---|
| Account budget `javabin-account-monthly` | `terraform/platform/cost-analytics/main.tf` | Forecast for the month > 100%; actual > 100%; actual > 150% |
| Cost Anomaly Detection `javabin-anomaly-monitor` | `terraform/platform/monitoring/main.tf` | Per-service anomaly with total impact >= $10 (IMMEDIATE) |
| Team budgets `javabin-team-<team>` | `team-provisioner` Lambda (from registry `budget_nok`, default 500 NOK) | Actual > 80% (Slack); actual > 200% triggers `budget-enforcer` |
| `budget-enforcer` | `terraform/lambda-src/budget_enforcer` | Suspends the team: ECS services to `desired_count=0`, RDS instances stopped (nothing is deleted). See [Budget suspension](#budget-suspension) |
| `daily-cost-check` / `cost-report` | `terraform/lambda-src/` | Daily spike check; weekly breakdown every Monday |

The account budget limit is `account_budget_usd` in `terraform/platform/main.tf`
(currently $600, just above steady state). **Lower it as the legacy cleanup
lands**, otherwise it stops catching drift. A budget that alerts every month is
noise; one set far above spend is useless.

There is deliberately no CloudWatch `EstimatedCharges` billing alarm. That
metric needs "Receive Billing Alerts" enabled on the account and only exists in
us-east-1; the old alarm sat in `INSUFFICIENT_DATA` with an SNS topic nobody
subscribed to. The budget's FORECASTED notification is the early warning.

A per-team anomaly monitor was considered and skipped: a tag-based
`aws_ce_anomaly_monitor` needs every team value listed explicitly and is
replaced whenever the list changes, and per-team budgets already cover it.

## Budget suspension

When a team's actual spend passes 200% of its monthly budget, AWS Budgets
notifies `javabin-budget-enforcement` and `budget-enforcer`:

1. Writes the marker `/javabin/budget-suspensions/{team}` (JSON: when, which
   budget, each service's previous desired count, the RDS instances it stopped).
2. Sets every ECS service tagged `team={team}` in `javabin-platform` to
   `desired_count=0`.
3. Stops every RDS instance tagged `team={team}` that is `available` (Aurora
   members and read replicas are skipped; they cannot be stopped this way).
4. Posts to #javabin-cost-alerts.

**The suspension sticks.** The `ecs-service` module reads the marker and plans
`desired_count = 0` while it exists, so the team's next `terraform apply` or
deploy does not scale the services back up (`ecs-deploy` only forces a new
deployment; it never changes the count). Every 6 hours a reconcile run re-zeros
services scaled up by hand and stops the databases again: RDS starts a stopped
instance by itself after 7 days, and reconcile catches that within 6 hours.
Terraform changes to a stopped RDS instance fail until it is started.

**How a team gets unsuspended** (any of):

- **Raise the budget.** Raise `budget_nok` in the team's registry YAML. Once
  team-provisioner has updated the budget, the next reconcile sees spend under
  200% of the new limit and lifts the suspension (within 6 hours).
- **Wait for the next month.** Budgets reset on the 1st (UTC); the first
  reconcile of the month lifts every suspension.
- **Lift it now.** A platform admin invokes the Lambda:
  `aws lambda invoke --function-name javabin-budget-enforcer --payload '{"action":"lift","team":"<team>"}' --cli-binary-format raw-in-base64-out /dev/stdout`.
  AWS Budgets notifies once per threshold per month, so a manual lift holds
  until next month even if spend stays above 200%.

Lifting deletes the marker, sets each service the enforcer scaled back to its
previous desired count (if it is still at 0), starts the RDS instances it
stopped, and posts to #javabin-cost-alerts. Services created during the
suspension come up on their next deploy. If the team's budget cannot be read,
reconcile keeps the suspension rather than lifting it.

The marker is not tamper-proof: it is untagged so that every team's Terraform
can read it, which also means a team role could delete it. The developer
boundary is ~45 bytes under AWS's 6,144-character limit, so a Deny for the
`budget-suspensions` path does not fit yet. Deleting the marker stops
reconcile from enforcing the suspension and shows in CloudTrail.

## Attribution: cost-allocation tags

`team`, `service`, `environment`, `managed-by` and `repo` are activated as
cost-allocation tags (`aws_ce_cost_allocation_tag.tags` in
`monitoring/main.tf`). Apps deployed through `app.yaml` get them from the
generated module's `default_tags`, and ECS tasks inherit them through
`propagate_tags = "SERVICE"`.

Spend without a `team` tag is expected for: tax, AWS Config, Security Hub,
public IPv4 charges, the shared NAT gateway and ALB, and every legacy
pre-platform stack. In Cost Explorer group by `Tag: team` and read the
"No tag key" bucket as "platform overhead + legacy".

## Detailed analysis: CUR + Athena

CUR 2.0 (daily, with resource IDs) is exported to `javabin-cur-<account>`,
crawled by Glue into the `javabin_cur` database, and queryable from the
`javabin-cost-analytics` Athena workgroup (100 MB scan limit per query).
Use it for per-resource questions Cost Explorer cannot answer.

## Built-in cost controls

| Control | Where | Setting |
|---|---|---|
| Platform Lambda log retention | `terraform/platform/lambdas/log-groups.tf`, `identity/log-groups.tf` | 365 days. Every new Lambda must be added to `local.platform_lambda_names` (`scripts/tests/test_lambda_log_groups.py` enforces it) |
| App log retention | `terraform/modules/ecs-service` | 30 days |
| ECR image lifecycle | `terraform/modules/ecr-repo`, CI repos in `compute` | Expire old/untagged images |
| AWS Config recording | `terraform/platform/monitoring/main.tf` | All supported types; `AWS::EC2::NetworkInterface`, VPC and Subnet recorded daily. See below |
| Config history bucket | `monitoring/main.tf` | Objects expire after 730 days |
| CUR bucket | `cost-analytics/main.tf` | IA after 90 days, expire after 365 |
| Athena results | `cost-analytics/main.tf` | Expire after 7 days |

### Why Config records ENIs daily

AWS Config charges per configuration item ($0.003 each). Every Fargate task
start/stop creates and deletes an ENI in the platform private subnets, and
each change re-records the ENI plus its VPC and subnet. In Sept 2026 the
account recorded ~13k items ($39); ENI, VPC and Subnet were ~7.3k of them
(~$22), almost all caused by one crash-looping service (`testteam-platform-test-app`,
~80 task starts a day).

ENIs are **not** excluded from recording, because that would weaken Security
Hub:

- **EC2.180** (ENI source/destination check) is enabled and its Config rule is
  scoped to `AWS::EC2::NetworkInterface`. Without ENI recording it goes stale.
- **Config.1** fails when Config does not record every resource type an
  enabled control needs. (It already fails today for IAM global types and the
  custom recorder role; excluding ENIs would add a third reason.)

Instead ENI, VPC and Subnet use a DAILY recording override: at most one item
per changed resource per day, and EC2.180 still evaluates every ENI that
exists at the daily snapshot. Short-lived ENIs created and deleted within the
same day may not be recorded at all. Security groups stay continuous because
SG changes are security-relevant.

The saving depends on churn. With the crash loop it is roughly $15–20/month;
once the crash loop is fixed, steady-state churn is a few dollars a month at
most. Fixing the crashing service is the bigger lever.

After apply, check for 7 days:

- `AWS/Config` `ConfigurationItemsRecorded` per `ResourceType`. If
  `AWS::EC2::SecurityGroup` dominates, consider adding it to the override.
- Security Hub **EC2.180** still has fresh evaluations (not NO_DATA).
- Security Hub **Config.1** has no new reason mentioning
  `AWS::EC2::NetworkInterface`.

### Account budget

`account_budget_usd` is set about 10% above steady-state spend, with
FORECASTED >100% and ACTUAL >100% notifications. Lower it each time a cleanup
from the cost audit lands, or the budget stops catching real overruns.

## Known savings outside platform Terraform

The 2026-10 cost audit (kept outside the repo, in the maintainers'
`explore/cost-audit-report.md`) lists legacy resources that cost money and are
not owned by platform Terraform: idle Elastic Beanstalk environments, the
legacy `Default-ALB` and its public IPs, the Coolify host, the NAT gateway
design, and Savings Plan timing. Those are cleanup decisions for their owners,
not things the platform changes automatically.
