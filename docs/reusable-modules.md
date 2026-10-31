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

**Required inputs:** `name`, `team`, `vpc_id`, `https_listener_arn`, `listener_rule_priority`, `host_header`, `route53_zone_id`, `alb_dns_name`, `alb_zone_id`
**Optional inputs:** `project`, `login_rules` (app.yaml `routing.login`), `login_session_timeout` (default 8 h), `login_client_wait` (default `60s`), `allow_dns_overwrite` (default false; take over an existing A record for `host_header` with a Route53 UPSERT, set by `routing.adopt_dns` in app.yaml)
**Outputs:** `target_group_arn`, `target_group_arn_suffix`, `dns_name`

`login_rules` (map keyed `p1`, `p2`, ...) adds one listener rule per entry, matching host + paths at the entry's priority (below `listener_rule_priority`). Protected entries get an `authenticate-oidc` action against the chosen Cognito pool, with a per-rule client `{team}-{name}.login-N` requested through a nested `cognito-app-client` (slot `login-N`); group restrictions are enforced by Cognito (auth-claims). `public` entries forward without sign-in. The host rule `depends_on` them, so a new app serves nothing until they exist. `authenticate-cognito` is not used: it needs `cognito-idp:DescribeUserPoolClient` on the platform pools, which the developer boundary denies. Requires the `hashicorp/time` provider (for the wait on the async client creation).

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

`service_registry_arns` (list, default `[]`) registers the tasks in Cloud Map (from `app-network`).

**Outputs:** `service_name`, `task_definition_arn`, `log_group_name`

## service-bucket

S3 bucket with versioning, encryption, public access block, optional lifecycle expiry.

**Naming:** `{team}-{service}-{name}-{account_id}` (`service` is the app name)
**Outputs:** `bucket_name`, `bucket_arn`, `access_policy_json`

## service-database

DynamoDB table with configurable keys, billing mode, TTL, PITR, encryption.

**Naming:** `{team}-{service}-{name}`
**Outputs:** `table_name`, `table_arn`, `access_policy_json`

## service-secret

SSM Parameter Store SecureString. Value is set manually after creation.

**Naming:** `/{project}/apps/{team}/{service}/{name}`
**Outputs:** `parameter_arn`, `parameter_name`, `access_policy_json`

## service-queue

SQS queue + dead-letter queue with configurable retention and visibility timeout.

**Naming:** `{team}-{service}-{name}` (queue), `{team}-{service}-{name}-dlq` (DLQ)
**Outputs:** `queue_url`, `queue_arn`, `dlq_url`, `dlq_arn`, `access_policy_json`

## service-rds

RDS PostgreSQL instance in private subnets.

**Inputs:**

| Input | Default |
|-------|---------|
| `name` | required — database purpose; identifier `{team}-{service}-{name}`, database name `name` with `-` → `_` |
| `team`, `service`, `project` | required |
| `master_username` | `postgres` |
| `engine_version` | `"16"` |
| `instance_class` | `db.t3.micro` |
| `allocated_storage` | 20 GB |
| `subnet_ids` | required |
| `vpc_id` | required |
| `allowed_security_group_ids` | required |
| `backup_retention_period` | 7 |
| `multi_az` | false |
| `deletion_protection` | true |

**Password:** Generated once via `random_password`, stored in SSM at `/{project}/apps/{team}/{service}/{name}/db-master-password`. Password authentication only: IAM database authentication is not enabled.

**Outputs:** `endpoint` (host:port), `address`, `port`, `db_name`, `username`, `url` (`postgresql://host:port/db_name`), `jdbc_url` (`jdbc:postgresql://host:port/db_name`), `password_parameter_arn`, `access_policy_json`, `security_group_id`. The URLs carry no credentials.

**Auto-wiring:** `access_policy_json` grants `ssm:GetParameter` on the password parameter (attached to the task role). The generator maps `env` → `url`, `jdbc_env` → `jdbc_url`, `host_env` → `address`, `port_env` → `port`, `user_env` → `username`, and `password_env` → `password_parameter_arn` as an ECS secret.

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

**Manages:** tags, `deletion_protection`, `backup_retention_period` (when set), final-snapshot settings (`<identifier>-final-<random hex>`, so a later delete/re-adopt cycle never collides with an old snapshot). Needs AWS provider 5.40 or later. Every other argument is read from the live instance or listed in `ignore_changes`, so importing never plans a replacement. `prevent_destroy` is set.

**Outputs:** `endpoint`, `address`, `port` (string), `arn`, `resource_id`

**Auto-wiring:** none. The app keeps its own database name and credentials (`secrets:`); `host_env` and `port_env` map to `address` and `port`. `env`, `jdbc_env`, `user_env` and `password_env` are rejected with `adopt`.

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

**Inputs:** `app_name`, `team`, `pool` (internal/external), `client_type` (confidential/public/none), `callback_urls`, `logout_urls`, `allowed_oauth_scopes`, `allowed_groups`, `external_pool_custom_domain`, `slot` (optional `login-N`: an extra client `{team}-{app_name}.login-N` with everything under `/{project}/apps/{team}/{app_name}/login-N/`)
**Outputs:** `user_pool_id`, `user_pool_domain`, `issuer_url`, `well_known_url`, `client_id_arn`, `client_secret_arn`, `identity_provider_arn` (SSM ARNs for ECS `secrets`), `auth_status_parameter_name`, `client_id_parameter_name`, `client_secret_parameter_name`, `request_parameter_name`, `request_value` (the request JSON; depends on the parameter existing)

## app-network

Emitted for every app. The app's own task security group `{team}-{name}-tasks` (ALB ingress on the
app port when routed, all egress, tag `javabin-network=app`), its Cloud Map service `{team}-{name}`
in the shared `{project}.internal` namespace, and the app-to-app request
`/{project}/apps/{team}/{name}/network` (security group, port, `calls`, `provides`). The platform
`app-peers-sync` Lambda turns the requests into security group rules, Cognito M2M clients and the
caller's `peer-*-url` / `peer-*-scopes` / `m2m-client-*` parameters. See
[app-yaml-reference.md#calls--provides-app-to-app](app-yaml-reference.md#calls--provides-app-to-app).

**Inputs:** `name`, `team`, `project`, `vpc_id`, `port`, `alb_ingress`, `calls` (`"team/app"` => scopes),
`provides_scopes` (scope => description), `consumers` (`"team/app"` => scopes), `internal_pool_name`,
`internal_pool_domain` (Cognito domain prefix for `token_url`, default `javabin-internal`)
**Outputs:** `security_group_id`, `registry_arn`, `dns_name`, `request_parameter_name`, `peer_url_arns`, `peer_scopes_arns`,
`m2m_client_id_arn`, `m2m_client_secret_arn`, `token_url`, `issuer_url`, `jwks_url`, `scope_prefix`

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
