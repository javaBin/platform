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

```yaml
team: core
```

Used for: resource tagging, budget allocation, access control.

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

DynamoDB tables.

```yaml
resources:
  databases:
    - name: sessions
      hash_key: id          # required
      range_key: timestamp  # optional
      env: SESSIONS_TABLE
```

#### secrets

Secrets Manager secrets. Value is set manually after creation.

```yaml
resources:
  secrets:
    - name: api-key
      env: API_KEY          # injected via ECS secrets (ARN reference)
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

Cognito user pool client registration. Creates app credentials and stores them in SSM at `/{project}/platform-apps/{name}/cognito-{pool}-client-{id,secret}`.

```yaml
auth: internal    # options: internal, external, both, none
```

- `internal` — Javabin members (Google Workspace SSO)
- `external` — public users (self-registration)
- `both` — creates clients in both pools
- `none` or omitted — no Cognito integration

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

### environments (planned — not yet supported)

Multi-environment configuration. If omitted, a single production environment is created.

> **Note:** This field is planned for a future release. The `app-stack` module and `generate-terraform.sh` do not yet read the `environments` key. For now, all apps deploy as a single production environment.

```yaml
environments:
  prod:
    compute:
      cpu: 512
      memory: 1024
    routing:
      host: submit.javazone.no
      priority: 100
  dev:
    compute:
      cpu: 256
      memory: 512
    routing:
      host: dev.submit.javazone.no
      priority: 101
    budget_alert_nok: 200
```

Each environment inherits top-level defaults and can override: `compute`, `routing`, `resources`, `environment`, `budget_alert_nok`, `alarms`.

## How It Works

1. Developer creates/updates `app.yaml`
2. CI runs `scripts/generate-terraform.sh` which produces `terraform/backend.tf`, `providers.tf`, `main.tf`
3. Generated files call the `app-stack` module which reads `app.yaml` and creates all resources
4. CI runs `terraform plan` → LLM review → `terraform apply`

Generated files have a `# GENERATED FROM app.yaml` marker. The script only overwrites files with this marker. Developers can add custom `.tf` files alongside the generated ones.

## Naming Conventions

| Resource | Name Pattern |
|----------|-------------|
| ECS service | `{name}` |
| ECR repo | `{name}` |
| S3 bucket | `javabin-{bucket_name}-{account_id}` |
| DynamoDB table | `javabin-{table_name}` |
| SQS queue | `javabin-{queue_name}` |
| Secrets Manager | `javabin/{secret_name}` |
| IAM task role | `javabin-{name}` |
| CloudWatch logs | `/ecs/javabin/{name}` |
| DNS record | `{routing.host}` |
| SSM (Cognito) | `/javabin/platform-apps/{name}/cognito-*` |
