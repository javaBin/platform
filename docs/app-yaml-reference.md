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
  host: moresleep.javazone.no  # required if routing is specified; also java.no / javabin.no
  priority: 100                # required, unique 1-50000
  deregistration_delay: 30     # default: 30 (seconds)
```

- `host` must be exactly one label under `javazone.no`, `java.no` or `javabin.no` (e.g. `moresleep.javazone.no`, `medlem.java.no`, `styret.javabin.no`). Those are the domains with a wildcard cert on the platform ALB; deeper names like `a.b.java.no` are rejected because the wildcard cert does not cover them.
- `host` creates a Route53 A record (in the zone matching the host's domain) pointing to the platform ALB and an ALB listener rule matching the Host header.
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

SSM Parameter Store SecureString parameters. The platform creates each one with the value `PLACEHOLDER`; set the real value with the CLI from the app repo, signed in with your team's SSO permission set (`javabin-team-<team>`):

```bash
javabin secret list            # which secrets still hold PLACEHOLDER
javabin secret set stripe-key  # prompts without echo; --stdin to pipe it in
```

ECS reads secrets when a task starts, so the platform restarts the service (`javabin-app-secret-rollout`) within a minute of a secret changing. Only secrets the platform created can be set (deploy first), and Terraform never overwrites the value once set.

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
  client: confidential        # confidential (default) | public | none — see below
  callback_urls:              # optional; https on routing.host / extra_hosts, or http://localhost
    - https://app.javazone.no/auth/callback
    - http://localhost:3000/auth/callback
  logout_urls:                # optional; same rules
    - https://app.javazone.no/
  extra_hosts:                # optional — other hosts the app serves callbacks on
    - www.app.javazone.no
  scopes:                     # optional, default ['openid', 'email', 'profile']
    - openid
    - email
    - profile
  groups:                     # optional — only members of these groups can sign in
    - helter
  env:                        # optional — also inject values under your own names
    COGNITO_CLIENT_ID: NUXT_PUBLIC_OIDC_CLIENT_ID
    COGNITO_ISSUER_URL: NUXT_PUBLIC_OIDC_AUTHORITY
    COGNITO_IDENTITY_PROVIDER: NUXT_PUBLIC_OIDC_IDENTITY_PROVIDER
```

#### `scopes`

Any of `openid`, `email`, `profile`, `phone`, `aws.cognito.signin.user.admin`. The `internal` pool refuses `aws.cognito.signin.user.admin`: it lets a signed-in user change their own attributes, including the email the platform uses to look up group membership. A request with it is rejected and the client is not created or changed.

#### `client`

| Value | For | You get |
|---|---|---|
| `confidential` | Server-side login (the server does the code exchange) | Client ID **and** secret |
| `public` | Browser apps doing OIDC with PKCE (oidc-client-ts, etc.) — a secret can't be kept in a browser | Client ID, no secret |
| `none` | APIs that only *verify* tokens issued to another app's client | Pool/issuer values only; no client, no callbacks, no `routing.host` needed. `callback_urls`, `logout_urls`, `scopes` and `extra_hosts` are rejected (they would be ignored) |

A typical split frontend/backend: the frontend uses `client: public`, the API uses `client: none` and validates tokens against `COGNITO_ISSUER_URL` / `COGNITO_WELL_KNOWN_URL`.

A `client: none` API has no client ID of its own to check, so it accepts tokens issued to *any* client in the pool. Check `cognito:groups` (or the token's `client_id`) for anything that matters.

Changing `client` between `confidential` and `public` replaces the Cognito client, so its ID changes. The platform restarts the service when the new ID is written (app-secret-rollout), so running tasks don't keep the deleted ID. SPAs that bake the client ID in at *build* time (Vite `VITE_*`, Next `NEXT_PUBLIC_*` at build) must rebuild; runtime config (Nuxt `NUXT_PUBLIC_*`) picks it up on restart.

Both forms are supported and produce the same wiring.

- `internal` — Javabin heroes. Every internal client signs in through the pool's IdP list (`internal_identity_providers` in `terraform/platform/identity/`): today Google Workspace SAML (`GoogleSAML`), after the Google cutover only `Google`. With a single IdP the hosted UI sends users straight to it, so apps don't need an `identity_provider` parameter; apps that pass one anyway should read `COGNITO_IDENTITY_PROVIDER` instead of hardcoding `GoogleSAML`. There is no password form or sign-up. Only `@java.no` accounts get a token; anyone else is refused at sign-in.
- `external` — public users.
- `none` or omitted — no Cognito integration.

`auth:` with a client needs `routing.host` or explicit `callback_urls` — the defaults are derived from the host.

#### Defaults

If `callback_urls` is omitted, defaults to `[https://{routing.host}/, https://{routing.host}/auth/callback]`.
If `logout_urls` is omitted, defaults to `[https://{routing.host}/]`.

An explicit list *replaces* the defaults. Apps that redirect elsewhere (Spring: `/login/oauth2/code/cognito`, NextAuth: `/api/auth/callback/cognito`) must list that path.

#### URL rules (checked when terraform/ is generated)

- `https://`, except `http://localhost` / `http://127.0.0.1` for local development.
- The host must be `routing.host` or listed in `auth.extra_hosts` (localhost excepted), so a typo fails in the PR instead of as `redirect_mismatch` in the browser.
- No `#fragment` (also rejected by `app-auth-sync`).

The host rule runs when `app.yaml` is expanded (`generate-modules.py`); the platform Lambda checks only the scheme and fragments, so it is a guard against typos, not a security boundary.

#### Logout

Cognito's `/logout?client_id=…&logout_uri=…` redirects back only when `logout_uri` matches a `logout_urls` entry **exactly** (trailing slash and path included). Anything else lands on the hosted UI's `/login` page. Cognito ignores `post_logout_redirect_uri`; send `logout_uri`.

#### Sign-in denied

A user in none of `auth.groups` gets no token: Cognito redirects to your callback with `error=access_denied&error_description=PreTokenGeneration failed with error You are not in a group that has access to this app.` Render `error_description` on your callback page instead of looping back to sign-in.

#### How the client is created

App CI cannot create Cognito clients: the pools belong to the platform, and a grant on a pool would let any team rewrite every other team's client. So the app only *requests* one:

1. The generated Terraform writes the client config to SSM `/javabin/apps/{team}/{name}/auth`. The permission boundary lets a team write only under its own prefix, so the path proves ownership.
2. The platform Lambda `javabin-app-auth-sync` reacts to the change (EventBridge), creates or updates the client `{team}-{name}` in the requested pool, and writes `/javabin/apps/{team}/{name}/cognito-client-id`, `cognito-client-secret` and `cognito-client-identity-provider` (only when a value changes; a change restarts the service). It reports the outcome in `/javabin/apps/{team}/{name}/auth-status` (JSON, readable by the team): `status` (`synced` / `invalid` / `error` + `error`), `client_id`, `identity_providers`, `drift_corrected` (client settings someone changed outside the platform, reverted by the daily reconcile), `unknown_groups` (`auth.groups` entries that don't exist in the registry — nobody can sign in through them), `client_id_changed_at`, `synced_at`. Your team's SSO role cannot write it, but your CI role can (as it can the `cognito-client-*` parameters); the next sync overwrites any such change, so treat it as a report.
3. Deleting `auth:` deletes the request parameter, and the Lambda deletes the client and its credentials. A daily reconcile repairs anything an event missed.

On the very first deploy with `auth:`, the first task can start a few seconds before the credentials exist; ECS retries it.

#### auth doctor

`scripts/auth-doctor.py` (platform repo) checks the whole chain with the team's own credentials: the request matches app.yaml, `auth-status` is `synced` with the same client ID, `/oauth2/authorize` redirects to the IdP for every callback URL (no `redirect_mismatch`), `/logout` returns to every logout URL, and (warning only) the service's current deployment started after the client ID last changed. App CI runs it after `terraform apply` (non-blocking; failures show as annotations). Locally, from the app repo:

```bash
python3 ../platform/scripts/auth-doctor.py --app-yaml app.yaml   # add --json for scripts
```

#### Injected environment variables

| Var | Source | Notes |
|---|---|---|
| `COGNITO_USER_POOL_ID` | Pool looked up by name | Used to construct the issuer URL on the backend |
| `COGNITO_DOMAIN` | Hosted-UI FQDN of the pool | e.g. `javabin-internal.auth.eu-central-1.amazoncognito.com`; empty for external when no custom domain is configured |
| `COGNITO_ISSUER_URL` | `https://cognito-idp.{region}.amazonaws.com/{pool_id}` | OIDC issuer for JWT validation |
| `COGNITO_WELL_KNOWN_URL` | `{issuer}/.well-known/openid-configuration` | OIDC discovery document |
| `COGNITO_CLIENT_ID` | SSM (ECS task `secrets`) | `confidential` and `public` only |
| `COGNITO_CLIENT_SECRET` | SSM SecureString (ECS task `secrets`) | `confidential` only |
| `COGNITO_IDENTITY_PROVIDER` | SSM (ECS task `secrets`) | `confidential` and `public` only. The IdP the client signs in through (`GoogleSAML`, `Google`, or `COGNITO`), for apps that send `identity_provider` themselves. Changes at the IdP cutover, which restarts the service |
| `COGNITO_GROUPS` | Comma-joined list from `auth.groups` | Only when `groups` is set |

`auth.env` adds the same values under extra names (both names are injected). Only names injected for your `client` type can be aliased.

#### Groups

Tokens from the internal pool carry the user's registry groups in `cognito:groups` (ID and access token): every group in `javaBin/registry` `groups/groups.yaml` with `cognito: true` the user is a member of, plus `team-{team}` for teams they belong to. The `javabin-auth-claims` trigger adds them at every sign-in and refresh, from membership team-provisioner records on each registry merge. A registry change reaches tokens at the user's next sign-in or token refresh.

`auth.groups` restricts the client: a user in none of the listed groups is refused at sign-in, so the app never receives a token for them. The refusal (like the internal pool's `@java.no` check) comes back to your callback URL as `error=…&error_description=…PreTokenGeneration failed…`; show `error_description` to the user rather than a blank page. Apps should still check `cognito:groups` for finer-grained authorization (e.g. admin features).

No Cognito groups are created from `app.yaml`; add groups in the registry.

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
