# Reusable Terraform Modules

Modules under `terraform/modules/`. App repos source via:
```hcl
source = "git::https://github.com/javaBin/platform.git//terraform/modules/{name}?ref=main"
```

## platform-data

**Read-only data sources** — discovers shared platform infrastructure by naming convention. No resources created.

| Lookup | Name Pattern |
|--------|-------------|
| VPC | `javabin-vpc` |
| Subnets | tag `tier=private` / `tier=public` |
| Security group | `javabin-ecs-tasks-sg` |
| ALB | `javabin-alb` |
| ECS cluster | `javabin-platform` |
| Execution role | `javabin-ecs-execution` |
| Route53 zones | `javazone.no` (`route53_zone_id`); `javazone.no`, `java.no`, `javabin.no` (`route53_zone_ids` map) |

**Key outputs:** `vpc_id`, `private_subnet_ids`, `ecs_cluster_id`, `https_listener_arn`, `execution_role_arn`, `route53_zone_id`, `route53_zone_ids`

## ecr-repo

ECR repository with 4-rule lifecycle policy.

| Rule | Default |
|------|---------|
| Keep N semver releases (`v*`) | 5 |
| Keep N main builds (`main-*`, `master-*`) | 3 |
| Expire untagged | 1 day |
| Expire other tags | 7 days |

**Outputs:** `repository_url`, `repository_arn`

## service-routing

ALB target group + listener rule (host-header routing) + Route53 A record (alias to ALB).

**Required inputs:** `name`, `project`, `vpc_id`, `https_listener_arn`, `listener_rule_priority`, `host_header`, `route53_zone_id`, `alb_dns_name`, `alb_zone_id`
**Optional:** `allow_dns_overwrite` (default false) — take over an existing A record for `host_header` (Route53 UPSERT); set by `routing.adopt_dns` in app.yaml
**Outputs:** `target_group_arn`, `target_group_arn_suffix`, `dns_name`

## service-role

ECS task IAM role with CloudWatch Logs permissions + composable additional policies.

Pass `additional_policy_jsons` (map of name => JSON) to attach policies from other modules:
```hcl
additional_policy_jsons = {
  "bucket-data"    = module.bucket.access_policy_json
  "database-cache" = module.database.access_policy_json
}
```

**`trusted_services`** — controls which AWS service can assume the role. Default: `["ecs-tasks.amazonaws.com"]`. Can be set to `["ec2.amazonaws.com"]` or `["lambda.amazonaws.com"]` via `compute.trusted_service` in app.yaml. Enables cross-compute roles so EC2 instances and Lambda functions get the same auto-wired access policies.

**Outputs:** `role_arn`, `role_name`, `role_id`

## ecs-service

ECS Fargate task definition + service + CloudWatch log group.

Supports `environment` (map) and `secrets` (map of name => ARN) for container configuration.

**Tag propagation:** `enable_ecs_managed_tags = true` and `propagate_tags = "SERVICE"` ensure Fargate task-level compute costs are attributed to the team via Cost Explorer.

**Outputs:** `service_name`, `task_definition_arn`, `log_group_name`

## service-bucket

S3 bucket with versioning, encryption, public access block, optional lifecycle expiry.

**Naming:** `{project}-{name}-{account_id}`
**Outputs:** `bucket_name`, `bucket_arn`, `access_policy_json`

## service-database

DynamoDB table with configurable keys, billing mode, TTL, PITR, encryption.

**Naming:** `{project}-{name}`
**Outputs:** `table_name`, `table_arn`, `access_policy_json`

## service-secret

SSM Parameter Store SecureString. Value is set manually after creation.

**Naming:** `/{project}/apps/{service}/{name}`
**Outputs:** `parameter_arn`, `parameter_name`, `access_policy_json`

## service-queue

SQS queue + dead-letter queue with configurable retention and visibility timeout.

**Naming:** `{project}-{name}` (queue), `{project}-{name}-dlq` (DLQ)
**Outputs:** `queue_url`, `queue_arn`, `dlq_url`, `dlq_arn`, `access_policy_json`

## service-rds

RDS PostgreSQL instance in private subnets.

**Inputs:**

| Input | Default |
|-------|---------|
| `name` | required |
| `engine_version` | `"16"` |
| `instance_class` | `db.t3.micro` |
| `allocated_storage` | 20 GB |
| `subnet_ids` | required |
| `vpc_id` | required |
| `allowed_security_group_ids` | required |
| `backup_retention_period` | 7 |
| `multi_az` | false |
| `deletion_protection` | true |

**Password:** Generated once via `random_password`, stored in SSM at `/{project}/apps/{name}/db-master-password`.

**Outputs:** `endpoint`, `address`, `port`, `db_name`, `password_parameter_arn`, `access_policy_json`, `security_group_id`

**Auto-wiring:** `access_policy_json` grants `rds-db:connect` + `ssm:GetParameter` on the password parameter. Auto-attached to task role via `collect:access_policy_json`.

**app.yaml:**
```yaml
databases:
  - name: main
    engine: postgres
    instance_class: db.t3.micro
    allocated_storage: 20
    engine_version: "16"
```


## service-rds-adopted

An existing RDS instance brought under Terraform without changing it (`databases[].adopt` in app.yaml). The generated root module imports it with an `import {}` block.

**Inputs:**

| Input | Default |
|-------|---------|
| `identifier` | required — DB instance identifier |
| `deletion_protection` | true |
| `backup_retention_period` | null (keep the live value) |
| `tags` | `{}` |

**Manages:** tags, `deletion_protection`, `backup_retention_period` (when set), final-snapshot settings. Every other argument is read from the live instance or listed in `ignore_changes`, so importing never plans a replacement. `prevent_destroy` is set.

**Outputs:** `endpoint`, `address`, `port` (string), `arn`, `resource_id`

**Auto-wiring:** none. The app keeps its own credentials (`secrets:`); `env`, `host_env` and `port_env` map to `endpoint`, `address` and `port`.

## service-alarm

CloudWatch alarms for ECS services: CPU high, memory high, unhealthy targets, 5xx errors.

**Outputs:** `cpu_alarm_arn`, `memory_alarm_arn`

## app-stack

**Golden path module** — reads `app.yaml`, creates all infrastructure automatically.

Instantiates: platform-data, ecr-repo, service-routing, service-bucket (for_each), service-database (for_each), service-secret (for_each), service-queue (for_each), service-role (auto-composed policies), ecs-service, service-alarm.

Auto-wires: resource env vars (bucket names, table names, queue URLs), secret ARNs, IAM policies.

**Config:** `app.yaml` at repo root. See [app-yaml-schema](#app-yaml-schema) below.

## cognito-app-client

Requests a Cognito OAuth client for the app. It does **not** create the client: it writes the request to SSM `/{project}/apps/{team}/{app_name}/auth`, and the platform `app-auth-sync` Lambda creates the client `{team}-{app_name}` and writes its credentials next to the request (`cognito-client-id`, `cognito-client-secret`). See [app-yaml-reference.md#auth](app-yaml-reference.md#auth) for why.

**Inputs:** `app_name`, `team`, `pool` (internal/external), `client_type` (confidential/public/none), `callback_urls`, `logout_urls`, `allowed_oauth_scopes`, `allowed_groups`, `external_pool_custom_domain`
**Outputs:** `user_pool_id`, `user_pool_domain`, `issuer_url`, `well_known_url`, `client_id_arn`, `client_secret_arn` (SSM ARNs for ECS `secrets`), `request_parameter_name`

## Policy Composition Pattern

Resource modules export `access_policy_json`. The `service-role` module accepts a map of these. `scripts/generate-modules.py` composes all policies from declared resources.

```
app.yaml declares bucket "data" + database "sessions"
  → generate-modules.py emits both resource modules
  → collects access_policy_json from each
  → passes merged map to service-role
  → ECS task role gets exactly the permissions it needs
```

## app.yaml Schema

See [app-yaml-reference.md](app-yaml-reference.md) — the single source of truth for fields and defaults.
