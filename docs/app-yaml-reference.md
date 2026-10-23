# app.yaml Reference

Every app repo has an `app.yaml` at the repo root. It declares what infrastructure the app needs. The platform CI pipeline reads it, generates Terraform, and applies it automatically.

`app.yaml` is validated strictly: an unknown field, a typo, or a field listed under [Not supported](#not-supported) fails the `expand` step with a message naming the field. Nothing is silently ignored.

## Quick Start

Minimal `app.yaml` for a web service:
```yaml
name: moresleep
team: core
routing:
  host: moresleep.javazone.no
  priority: 120
```

Without `routing:` the service runs with no load balancer (a worker).

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

Service name. Starts with a letter; lowercase letters, digits, and hyphens. Max 20 characters. Defaults to the repo name.

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
  health_check: /health  # default: /health — ALB target group health check path
  health_check_matcher: "200"      # default: "200"
  user: "1000"                     # default: "1000" — container user (non-root)
  readonly_root_filesystem: true   # default: true — only /tmp is writable
```

Images that write outside `/tmp` (caches, uploads to local disk) need `readonly_root_filesystem: false`, or their tasks crash on start; the deployment circuit breaker then rolls the deploy back.

Valid CPU/memory combinations follow [Fargate task size constraints](https://docs.aws.amazon.com/AmazonECS/latest/developerguide/task-cpu-memory-error.html).

### routing

ALB routing configuration.

```yaml
routing:
  host: moresleep.javazone.no  # required if routing is specified
  priority: 100                # required, unique 1-50000
  deregistration_delay: 30     # default: 30 (seconds)
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
    - name: data          # bucket: {team}-{name}-{account_id}
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

Registers the app as an OAuth client in the platform's internal or external Cognito user pool and injects the client credentials into the container.

Shorthand:

```yaml
auth: internal    # uses defaults below
```

Full form:

```yaml
auth:
  pool: internal              # 'internal' (default) or 'external'
  callback_urls:              # optional, https only
    - https://app.javazone.no/auth/callback
  logout_urls:                # optional, https only
    - https://app.javazone.no/
  scopes:                     # optional, default ['openid', 'email', 'profile']
    - openid
    - email
    - profile
  groups:                     # optional, reference-only — exposed as COGNITO_GROUPS env var
    - admins
    - editors
```

Both forms are supported and produce the same wiring.

- `internal` — Javabin heroes. Sign-in offers every identity provider on the pool (Google Workspace SAML) plus Cognito users.
- `external` — public users.
- `none` or omitted — no Cognito integration.

`auth:` requires `routing.host` — callback and logout URL defaults are derived from it.

#### Defaults

If `callback_urls` is omitted, defaults to `[https://{routing.host}/, https://{routing.host}/auth/callback]`.
If `logout_urls` is omitted, defaults to `[https://{routing.host}/]`.

#### How the client is created

App CI cannot create Cognito clients: the pools belong to the platform, and a grant on a pool would let any team rewrite every other team's client. So the app only *requests* one:

1. The generated Terraform writes the client config to SSM `/javabin/apps/{team}/{name}/auth`. The permission boundary lets a team write only under its own prefix, so the path proves ownership.
2. The platform Lambda `javabin-app-auth-sync` reacts to the change (EventBridge), creates or updates the client `{team}-{name}` in the requested pool, and writes `/javabin/apps/{team}/{name}/cognito-client-id` and `cognito-client-secret`.
3. Deleting `auth:` deletes the request parameter, and the Lambda deletes the client and its credentials. A daily reconcile repairs anything an event missed.

On the very first deploy with `auth:`, the first task can start a few seconds before the credentials exist; ECS retries it.

#### Injected environment variables

| Var | Source | Notes |
|---|---|---|
| `COGNITO_USER_POOL_ID` | Pool looked up by name | Used to construct the issuer URL on the backend |
| `COGNITO_DOMAIN` | Hosted-UI FQDN of the pool | e.g. `javabin-internal.auth.eu-central-1.amazoncognito.com`; empty for external when no custom domain is configured |
| `COGNITO_ISSUER_URL` | `https://cognito-idp.{region}.amazonaws.com/{pool_id}` | OIDC issuer for JWT validation (`{issuer}/.well-known/openid-configuration`) |
| `COGNITO_CLIENT_ID` | SSM (ECS task `secrets`) | The OAuth client ID |
| `COGNITO_CLIENT_SECRET` | SSM SecureString (ECS task `secrets`) | Always generated — server-side apps only |
| `COGNITO_GROUPS` | Comma-joined list from `auth.groups` | Optional; for app-level authorization on the `cognito:groups` JWT claim |

#### Groups are reference-only

`auth.groups` is a list of group names that the app *expects* to see on the `cognito:groups` JWT claim. **No Cognito groups are created or modified from `app.yaml`.** Groups are managed by the team-provisioner Lambda (e.g. `team-{team}` groups synced from the registry).

#### `pool: both` is not supported

Apps that need users from both pools should run two services for now.

### domain

Convenience alias for `routing.host`. If both are set, `routing.host` takes precedence. `routing.priority` is still required.

```yaml
domain: cake-redux.javazone.no
routing:
  priority: 110
```

### environment

Additional environment variables injected into the container.

```yaml
environment:
  LOG_LEVEL: info
  JAVA_OPTS: -Xmx768m
```

Resource env vars (`env` fields on buckets/databases/queues) are auto-wired separately. This section is for custom application config. Numbers and booleans are passed as strings (`"4"`, `"true"`).

### alarms

CloudWatch alarms for ECS service health.

```yaml
alarms:
  enabled: true        # default: true
  cpu_threshold: 80    # default: 80 (percent)
  memory_threshold: 80 # default: 80 (percent)
  error_5xx_threshold: 10 # default: 10
```

Alarms are created only for services with `routing:`.

## Not supported

These fields were documented earlier but are not implemented. Using them fails `expand` with an explanation.

| Field | Instead |
|---|---|
| `environments` | One environment per app.yaml. For a dev environment, deploy a second app (e.g. `name: myapp-dev`) from its own repo. |
| `budget_alert_nok` | Budgets are per team: set `budget_nok` in `javaBin/registry` `teams/<team>.yaml`. |
| `auth.pool: both` | Two services, one per pool. |

## How It Works

1. Developer creates/updates `app.yaml`
2. The `expand` job runs `scripts/generate-modules.py`, which validates `app.yaml` and writes `terraform/backend.tf`, `providers.tf`, `main.tf`, `outputs.tf` (module blocks sourced from `javaBin/platform//terraform/modules/*`), then commits them to the branch
3. `tf-plan` plans the committed files → LLM review → `tf-apply` on main
4. `docker-build` pushes `{team}-{name}` to ECR, `ecs-deploy` rolls the service

Generated files carry a `# GENERATED FROM app.yaml` marker and are overwritten on every run. Developers can add their own `.tf` files alongside them.

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
| SSM (Cognito) | `/javabin/apps/{team}/{name}/cognito-client-{id,secret}` |
