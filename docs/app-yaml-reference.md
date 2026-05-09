# app.yaml Reference

Every app repo has an `app.yaml` at the repo root. It declares what infrastructure the app needs. The platform CI pipeline reads it, generates Terraform, and applies it automatically.

## Quick Start

Minimal `app.yaml` for a web service:
```yaml
name: moresleep
team: core
```

Full example:
```yaml
name: cake-redux
team: pkom
compute:
  cpu: 512
  memory: 1024
  port: 8080
  desired_count: 2
  health_check: /health
routing:
  host: cake-redux.javazone.no
  priority: 110
resources:
  buckets:
    - name: uploads
      env: UPLOADS_BUCKET
  databases:
    - name: sessions
      hash_key: id
      env: SESSIONS_TABLE
  secrets:
    - name: stripe-key
      env: STRIPE_KEY
  queues:
    - name: notifications
      env: NOTIFICATIONS_QUEUE_URL
auth: internal
domain: cake-redux.javazone.no
budget_alert_nok: 500
environment:
  LOG_LEVEL: info
  JAVA_OPTS: -Xmx768m
alarms:
  enabled: true
  cpu_threshold: 80
  memory_threshold: 80
```

## Fields

### name (required)

Service name. Lowercase letters, digits, and hyphens. Max 20 characters.

```yaml
name: moresleep
```

Used for: ECS service name, ECR repo name, IAM role names, CloudWatch log groups, DNS records, SSM parameter paths.

### team (required)

Team that owns this service. Must match a file in `javaBin/registry/teams/`.

Team names must be **lowercase letters only** (a-z), no hyphens, digits, or symbols. Max 12 characters. This constraint ensures resource names fit within AWS limits (e.g. ALB target groups: 32 chars).

```yaml
team: core
```

Used for: resource name prefix (`{team}-{service}`), ABAC tagging, budget allocation, access control.

### compute

Container compute configuration. All fields have defaults.

```yaml
compute:
  cpu: 512            # default: 512 (0.5 vCPU)
  memory: 1024        # default: 1024 (1 GB)
  port: 8080          # default: 8000
  desired_count: 1    # default: 1
  health_check: /health  # default: /health
```

Valid CPU/memory combinations follow [Fargate task size constraints](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-cpu-memory-error.html).

### routing

ALB routing configuration.

```yaml
routing:
  host: moresleep.javazone.no  # required if routing is specified
  priority: 100                # required, unique 1-50000
```

- `host` creates a Route53 A record pointing to the platform ALB and an ALB listener rule matching the Host header.
- `priority` determines listener rule evaluation order. Must be unique across all apps. Coordinate via the registry.

### resources

Optional infrastructure resources. Each creates the AWS resource and auto-wires environment variables and IAM policies to the ECS task.

#### buckets

S3 buckets with versioning and encryption.

```yaml
resources:
  buckets:
    - name: data          # bucket: javabin-{name}-{account_id}
      env: DATA_BUCKET    # env var injected into container
```

#### databases

DynamoDB tables (default) or RDS PostgreSQL instances.

```yaml
resources:
  databases:
    - name: sessions
      hash_key: id          # required (DynamoDB)
      range_key: timestamp  # optional (DynamoDB)
      env: SESSIONS_TABLE

    - name: main
      engine: postgres              # "dynamodb" (default) or "postgres"/"postgresql"
      instance_class: db.t3.micro   # RDS only, default: db.t3.micro
      allocated_storage: 20         # GB, RDS only, default: 20
      engine_version: "16"          # PostgreSQL version, RDS only, default: "16"
      backup_retention_period: 7    # days, RDS only, default: 7
      multi_az: false               # RDS only, default: false
      deletion_protection: true     # RDS only, default: true
      env: DATABASE_URL
```

DynamoDB and PostgreSQL entries can coexist in the same `databases` list. Entries without `engine` (or with `engine: dynamodb`) use the DynamoDB module. Entries with `engine: postgres` or `engine: postgresql` use the RDS module.

RDS instances generate a master password at creation time, stored in SSM Parameter Store at `/{project}/apps/{name}/db-master-password`. The ECS task role automatically receives IAM policies for `rds-db:connect` and `ssm:GetParameter` on the password parameter.

#### secrets

SSM Parameter Store SecureString parameters. Value is set manually after creation.

```yaml
resources:
  secrets:
    - name: api-key
      env: API_KEY          # injected via ECS secrets (SSM parameter ARN reference)
```

#### queues

SQS queues with automatic dead-letter queue.

```yaml
resources:
  queues:
    - name: tasks
      env: TASKS_QUEUE_URL
```

### auth

Registers the app as a Cognito client in the platform's internal or external user pool, stores the credentials in SSM, and injects them into the container as environment variables.

Shorthand:

```yaml
auth: internal    # uses defaults below
```

Full form:

```yaml
auth:
  pool: internal              # 'internal' or 'external' (see "pool: both" below)
  callback_urls:              # optional
    - https://app.javazone.no/auth/callback
  logout_urls:                # optional
    - https://app.javazone.no/
  scopes:                     # optional, default ['openid', 'email', 'profile']
    - openid
    - email
    - profile
  groups:                     # optional, reference-only — exposed as COGNITO_GROUPS env var
    - admins
    - editors
```

A client secret is always generated — every app on this platform is a
server-side container.

- `internal` — Javabin members (Google Workspace SSO via SAML)
- `external` — public users (Google OAuth, self-registration)
- `none` or omitted — no Cognito integration

`auth:` requires `routing.host` — callback and logout URL defaults are derived from it.

#### Defaults

If `callback_urls` is omitted, defaults to `[https://{routing.host}/, https://{routing.host}/auth/callback]`.
If `logout_urls` is omitted, defaults to `[https://{routing.host}/]`.

#### Injected environment variables

When `auth:` is set, these env vars are injected into the container automatically:

| Var | Source | Notes |
|---|---|---|
| `COGNITO_USER_POOL_ID` | Looked up from the platform pool | Used to construct the issuer URL on the backend |
| `COGNITO_DOMAIN` | Hosted-UI FQDN of the pool | e.g. `javabin-internal.auth.eu-central-1.amazoncognito.com` (internal); empty for external when no custom domain is configured |
| `COGNITO_ISSUER_URL` | `https://cognito-idp.{region}.amazonaws.com/{pool_id}` | OIDC issuer for JWT validation |
| `COGNITO_CLIENT_ID` | SSM SecureString (via ECS task `secrets`) | The OAuth client ID |
| `COGNITO_CLIENT_SECRET` | SSM SecureString (via ECS task `secrets`) | Always generated — server-side apps only |
| `COGNITO_GROUPS` | Comma-joined list from `auth.groups` | Optional; for app-level authorization on `cognito:groups` JWT claim |

The credentials live in SSM at `/{project}/platform-apps/{name}/cognito-{pool}-client-{id,secret}`. The shared ECS execution role has read access to that path.

#### Groups are reference-only

`auth.groups` is a list of group names that the app *expects* to see on the `cognito:groups` JWT claim. **No Cognito groups are created or modified from `app.yaml`.** Groups themselves are managed by the team-provisioner Lambda (e.g. `team-{team}` groups synced from the registry). If you need a new group, add it via the registry or Cognito console out-of-band, then list it here so the app can read `COGNITO_GROUPS` for authorization checks.

#### `pool: both` is not yet supported

The generator rejects `auth.pool: both`. Apps that need to accept users from both pools should split into two clients with explicit env-var naming — defer this until a concrete need lands.

### domain

Convenience alias for `routing.host`. If both are set, `routing.host` takes precedence.

```yaml
domain: cake-redux.javazone.no
```

### budget_alert_nok

Monthly budget alert threshold in Norwegian Kroner. Default: 1000.

```yaml
budget_alert_nok: 500
```

Creates a Cost Anomaly Detection monitor and budget alert. Notifications go to the team's Slack channel.

### environment

Additional environment variables injected into the container.

```yaml
environment:
  LOG_LEVEL: info
  JAVA_OPTS: -Xmx768m
```

Resource env vars (`env` fields on buckets/databases/queues) are auto-wired separately. This section is for custom application config.

### alarms

CloudWatch alarms for ECS service health.

```yaml
alarms:
  enabled: true        # default: true
  cpu_threshold: 80    # default: 80 (percent)
  memory_threshold: 80 # default: 80 (percent)
```

### environments

Multi-environment configuration. If omitted, a single production environment is created (backwards compatible). When present, each key defines a separate environment with its own ECS service, ALB target group, DNS record, IAM role, and Terraform state.

```yaml
environments:
  prod:
    routing:
      host: submit.javazone.no
      priority: 100
  dev:
    routing:
      host: dev.submit.javazone.no
      priority: 101
    auto_deploy: true
```

Each environment inherits all top-level defaults and can override: `compute`, `routing`, `domain`, `resources`, `environment`, `budget_alert_nok`, `alarms`.

#### Environment defaults

The `dev` environment gets smaller defaults automatically:

| Field | dev default | other envs default |
|-------|-------------|-------------------|
| `compute.cpu` | 256 | 512 |
| `compute.memory` | 512 | 1024 |

All other fields (port, desired_count, health_check, etc.) use the same defaults regardless of environment.

#### Full example

```yaml
name: submittheforce
team: pkom
compute:
  cpu: 512
  memory: 1024
  port: 8080
resources:
  databases:
    - name: submissions
      hash_key: id
      env: SUBMISSIONS_TABLE
environment:
  LOG_LEVEL: info
environments:
  prod:
    routing:
      host: submit.javazone.no
      priority: 100
    compute:
      desired_count: 2
    environment:
      LOG_LEVEL: warn
  dev:
    routing:
      host: dev.submit.javazone.no
      priority: 101
    auto_deploy: true
```

This creates two environments:

| | prod | dev |
|---|---|---|
| CPU / Memory | 512 / 1024 (from top-level) | 256 / 512 (dev defaults) |
| Desired count | 2 (override) | 1 (default) |
| Domain | submit.javazone.no | dev.submit.javazone.no |
| DynamoDB table | `javabin-submittheforce-prod-submissions` | `javabin-submittheforce-dev-submissions` |
| ECS service | `submittheforce-prod` | `submittheforce-dev` |
| LOG_LEVEL | warn (override) | info (from top-level) |
| State key | `apps/submittheforce-prod/terraform.tfstate` | `apps/submittheforce-dev/terraform.tfstate` |

#### Override behavior

- **compute, routing, alarms**: Per-field override. Unspecified fields fall back to top-level, then to defaults.
- **resources**: Per-type replacement. Each resource type (buckets, databases, secrets, queues) is independently replaced — if the environment defines `resources.databases`, it gets only those databases (not merged with top-level). Resource types not defined in the environment inherit from top-level.
- **environment** (env vars): Merged. Top-level env vars are applied first, then environment-specific ones override on conflict.

#### auto_deploy

```yaml
environments:
  dev:
    auto_deploy: true
```

When `true`, merges to main automatically deploy to this environment without manual approval. Default: `false`. This is read by CI workflows, not by Terraform.

#### ECR repository

All environments share a single ECR repository named after the base app name. The same container image is deployed to all environments. The prod environment (or the first environment if no prod exists) creates the ECR repository; other environments reference it via data source.

**Important:** When first setting up multi-env, apply the environment that creates ECR (prod) before other environments.

#### Naming

Environment name must be lowercase alphanumeric, max 4 characters (e.g. `dev`, `prod`, `test`, `stag`). Multi-env apps should keep their name under ~19 characters to allow room for the environment suffix in ALB target group names (32-char AWS limit). Resources are named with an environment suffix:

| Resource | Single-env | Multi-env (dev) |
|----------|-----------|-----------------|
| ECS service | `moresleep` | `moresleep-dev` |
| IAM task role | `javabin-moresleep` | `javabin-moresleep-dev` |
| ALB target group | `javabin-moresleep` | `javabin-moresleep-dev` |
| DynamoDB table | `javabin-moresleep-sessions` | `javabin-moresleep-dev-sessions` |
| S3 bucket | `moresleep-data-{account}` | `moresleep-dev-data-{account}` |
| Log group | `/ecs/javabin/moresleep` | `/ecs/javabin/moresleep-dev` |
| ECR repo | `moresleep` | `moresleep` (shared) |

#### Generated directory structure

Single-env:
```
terraform/
  backend.tf
  providers.tf
  main.tf
```

Multi-env:
```
terraform/
  prod/
    backend.tf    # state key: apps/{name}-prod/terraform.tfstate
    providers.tf
    main.tf       # environment_name = "prod", create_ecr = true
  dev/
    backend.tf    # state key: apps/{name}-dev/terraform.tfstate
    providers.tf
    main.tf       # environment_name = "dev", create_ecr = false
```

## How It Works

1. Developer creates/updates `app.yaml`
2. CI runs `scripts/generate-terraform.sh` which produces `terraform/backend.tf`, `providers.tf`, `main.tf`
3. Generated files call the `app-stack` module which reads `app.yaml` and creates all resources
4. CI runs `terraform plan` → LLM review → `terraform apply`

Generated files have a `# GENERATED FROM app.yaml` marker. The script only overwrites files with this marker. Developers can add custom `.tf` files alongside the generated ones.

## Naming Conventions

All app resources are prefixed with the team name for ABAC enforcement.

| Resource | Name Pattern |
|----------|-------------|
| ECS service | `{team}-{name}` |
| ECR repo | `{team}-{name}` |
| ALB target group | `{team}-{name}` (max 32 chars, truncated+hashed if over) |
| S3 bucket | `{team}-{name}-{account_id}` |
| DynamoDB table | `{team}-{name}` |
| SQS queue | `{team}-{name}` |
| RDS instance | `{team}-{name}` |
| SSM (secrets) | `/javabin/apps/{team}/{service}/{secret_name}` |
| IAM task role | `{team}-{name}` |
| CloudWatch logs | `/ecs/{team}/{name}` |
| DNS record | `{routing.host}` |
| SSM (Cognito) | `/javabin/platform-apps/{name}/cognito-*` |

> In multi-env mode, `{name}` becomes `{name}-{env}` in all resource names except ECR. See [environments naming](#naming) for details.
