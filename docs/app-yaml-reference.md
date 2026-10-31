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
  desired_count: 1    # default: 1 — applied by ecs-deploy, not tf-apply
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
  adopt_dns: false             # default: false — take over an existing record for host
```

- `host` must be exactly one label under `javazone.no`, `java.no` or `javabin.no` (e.g. `moresleep.javazone.no`, `medlem.java.no`, `styret.javabin.no`). Those are the domains with a wildcard cert on the platform ALB; deeper names like `a.b.java.no` are rejected because the wildcard cert does not cover them.
- `host` creates a Route53 A record (in the zone matching the host's domain) pointing to the platform ALB and an ALB listener rule matching the Host header.
- `priority` determines listener rule evaluation order. Must be unique across all apps. Coordinate via the registry.
- `adopt_dns: true` takes over an A record for `host` that already exists (for example a hand-made record of an app moving onto the platform). It must be approved first: the host has to be listed for your team in `ADOPTIONS` in `scripts/registry.py` (a javaBin/platform PR), otherwise generation fails. Route53 records are not protected by team permissions, so this is what stops one team from taking over a hostname another app serves. Without `adopt_dns`, the apply fails because the record exists. With it, Route53 overwrites the record (UPSERT) to point at the platform ALB, and from then on Terraform manages it. The plan shows the record as a *create*. Changing `host` later replaces the record, and removing routing deletes it, as for any managed record. See [Adopting existing resources](#adopting-existing-resources).

#### routing.login — sign-in at the load balancer

`login` makes the platform load balancer require sign-in before a request reaches the app. You pick, per path, which Cognito pool users sign in to and which groups may pass. The app needs no auth code, so this works for simple apps and static web pages (see [recipe](#static-pages-behind-sign-in)). It is independent of [`auth`](#auth) (where the app does OIDC itself); an app can use both.

```yaml
routing:
  host: wiki.java.no
  priority: 120
  login:
    - paths: ["/admin/*"]          # rules are checked in this order; first match wins
      groups: [styret]
    - paths: ["/assets/*", "/health"]
      public: true                 # no sign-in for these
    - paths: ["/*"]                # default when paths is left out
      groups: [helter, team-core]
      pool: internal               # default
```

Shorthands:

```yaml
routing:
  login: internal          # whole site, any user of the internal pool
# or
routing:
  login:                   # one rule; paths defaults to ["/*"]
    groups: [helter]
```

| Field | Default | Meaning |
|---|---|---|
| `paths` | `["/*"]` | 1-4 ALB path patterns. Case-sensitive; `*` and `?` are wildcards; max 128 chars. On protected rules, `/x/*` also protects the bare `/x` (added for you unless you list `/x` yourself), and counts toward the 4. |
| `pool` | `internal` | `internal`: Javabin heroes, signed in through the internal pool's IdP list (Google Workspace). The pool has no self sign-up, its clients offer no username/password sign-in, and only `@java.no` accounts get a token. `external`: attendees, partners and other public users. Today that means Cognito email/password sign-up; other identity providers (e.g. Google, Vipps) are not set up on that pool yet. |
| `groups` | `[]` (any pool user) | Only users in at least one of these groups get through. Internal pool: registry groups (`groups/groups.yaml` with `cognito: true`, and `team-<team>`). External pool: native Cognito groups of the external pool. Nothing in the platform creates or fills those groups yet; until it does, an admin creates them by hand (`aws cognito-idp create-group` / `admin-add-user-to-group` on `javabin-external`), so ask the platform team. |
| `public` | `false` | Forward without sign-in. Cannot be combined with `pool` or `groups`. |

What happens:

- A user without a session is redirected to the pool's sign-in page, then back to the page they asked for. A user outside `groups` is refused by Cognito at sign-in ("You are not in a group that has access to this app.") and never gets a session, so the app is never reached. Where the message shows (Cognito's sign-in page or the load balancer's error page) depends on the sign-in flow.
- Every path not matched by a rule keeps working as before, without sign-in.
- Sessions last 8 hours. A registry change (e.g. removing someone from a group) takes effect at their next sign-in, so at most 8 hours later.
- An app.yaml change to a rule's `pool` or `groups`, or a reorder that gives a rule a new position, renames that rule's session cookie, so everyone signs in again and the new groups apply at once. Changing only `paths` keeps existing sessions.
- One sign-in covers all rules of the app: Cognito remembers the user, so a second rule only redirects.
- Internal pool: the load balancer sends `prompt=select_account`, so Google shows its account chooser (once the pool domain is on managed login; see `docs/cognito-google-setup.md`).

Rules and priorities:

- Each rule becomes its own ALB listener rule at the priorities **directly below** `routing.priority`: the first rule gets `priority - 1`, the next `priority - 2`, and so on. Leave that room free: if another app already uses one of those priorities, apply fails with `PriorityInUse`. `priority` must be larger than the number of rules. Nothing checks this across apps yet, so pick a `priority` with at least 10 free numbers below it (e.g. a multiple of 20 when registering a new app); apps whose priority is right next to another app's (e.g. 100 and 101) cannot add rules without moving.
- At most 10 rules. A path may appear only once, and a rule after a `/*` rule is an error (it could never match).
- Protect `/*` and open the exceptions with `public: true`, rather than protecting a few subpaths. The load balancer matches the raw request path: case-sensitively, without decoding `%xx` and without resolving `..`. An app or framework that does any of these can be reached past a subpath rule, e.g. `/Admin`, `/%61dmin/` or `/x/../admin/` past `/admin/*`. The generator warns when no rule covers `/*`.

What the app receives on signed-in requests (headers added by the load balancer):

| Header | Content |
|---|---|
| `x-amzn-oidc-identity` | The user's `sub` |
| `x-amzn-oidc-data` | JWT with the user's claims (`email`, `name`, ...), signed by the load balancer (ES256). Verify it with the key at `https://public-keys.auth.elb.eu-central-1.amazonaws.com/<kid>` and check that `signer` is the platform ALB's ARN. |
| `x-amzn-oidc-accesstoken` | The Cognito access token, with the user's groups in `cognito:groups`. Verify it against the pool's issuer (`COGNITO_ISSUER_URL` if the app also uses `auth`). |

Caveats:

- On paths that are not protected, a client can send these headers itself. Verify the signature before trusting them anywhere.
- The load balancer has no logout endpoint. To sign out, expire the `javabin-auth-p<N>-*` cookies and redirect to the pool's `/logout`.
- How it works: each protected rule has its own Cognito client, `<team>-<name>.login-<N>`, requested through SSM (`/javabin/apps/<team>/<name>/login-<N>/auth`) and created by the platform's `javabin-app-auth-sync` Lambda, exactly like [`auth`](#how-the-client-is-created). The first apply waits 60 seconds for the client. If it fails with `ParameterNotFound`, the client was not created in time: re-run the pipeline.

##### Static pages behind sign-in

There is no separate static-site hosting: serve the files from a small container and let `login` protect it. `login` protects apps that run on the platform; it cannot proxy to a site hosted elsewhere (the load balancer only forwards to its own target groups).

```dockerfile
FROM nginxinc/nginx-unprivileged:1.27-alpine
COPY site/ /usr/share/nginx/html/
```

```yaml
name: styret-docs
team: core
compute:
  cpu: 256
  memory: 512
  port: 8080
  health_check: /
  readonly_root_filesystem: false   # nginx writes its cache and pid outside /tmp
routing:
  host: styret.javabin.no
  priority: 130
  login:
    groups: [styret]
```

The health check comes from inside the VPC and is not affected by `login`.

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
      env: DATABASE_URL             # host:port
      host_env: DB_HOST             # optional, postgres only: hostname alone
      port_env: DB_PORT             # optional, postgres only: port alone
```

DynamoDB and PostgreSQL entries can coexist in the same `databases` list. Entries without `engine` (or with `engine: dynamodb`) use the DynamoDB module. Entries with `engine: postgres` or `engine: postgresql` use the RDS module.

RDS instances generate a master password at creation time, stored in SSM Parameter Store at `/{project}/apps/{name}/db-master-password`. The ECS task role automatically receives IAM policies for `rds-db:connect` and `ssm:GetParameter` on the password parameter.

`env` gets the endpoint as `host:port`. Apps that read host and port separately use `host_env` and `port_env` (PostgreSQL only, created or adopted).

##### Adopting an existing PostgreSQL instance

An app that moves onto the platform can keep its existing RDS instance instead of getting a new one:

```yaml
resources:
  databases:
    - name: main
      engine: postgres
      adopt: dbmoresleep            # DB instance identifier of the existing instance
      host_env: DBHOST
      port_env: DBPORT
      deletion_protection: true     # default: true
      backup_retention_period: 14   # optional; unset = keep the instance's current value
```

- The generated `main.tf` gets an `import {}` block, and the instance is imported into the app's Terraform state on the first apply. Nothing is created or moved. Once the instance is in state, Terraform skips the block, so it stays in the generated file.
- The `service-rds-adopted` module manages only tags, `deletion_protection` and (when set) `backup_retention_period`. Engine, version, class, storage, network placement, security groups, parameter group, password and monitoring stay as they are in AWS; `instance_class`, `allocated_storage`, `engine_version` and `multi_az` are rejected with `adopt`. The first plan shows `1 to import`, an in-place update of tags and deletion protection, one `random_id` (the suffix of the final snapshot name), and nothing replaced.
- The app keeps connecting the way it did, with its own credentials (put them in `secrets:`). No IAM policy or platform-managed password is added.
- The instance has `prevent_destroy`. To stop managing it without deleting it, add a hand-written file next to the generated ones, e.g. `terraform/release-db.tf`, with `removed { from = module.rds_main.aws_db_instance.this  lifecycle { destroy = false } }`, and remove the entry from `app.yaml` in the same change.
- Approval: each adoption is approved in a javaBin/platform PR that lists the identifier for your team in `ADOPTIONS` in `scripts/registry.py`; without it generation fails. Legacy instances are often untagged, and the team permission boundary only protects resources tagged with another team, so an unapproved `adopt` could take over (and tag away) another team's database. After the first apply the instance carries `team=<team>`.
- Network: platform tasks run in the platform VPC's private subnets and reach the internet through its NAT gateway (all database traffic then pays NAT data processing). An adopted instance in another VPC must be reachable from there. If that means a public endpoint, its security group should allow only the NAT address (and any legacy clients that still need it), never `0.0.0.0/0`. The platform does not change its security group, so tighten it yourself.

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

- `internal` — Javabin heroes. Every internal client signs in through the pool's IdP list (`internal_identity_providers` in `terraform/platform/identity/`): `Google` (Google OIDC; `GoogleSAML` before the cutover). With a single IdP the hosted UI sends users straight to it, so apps don't need an `identity_provider` parameter; apps that pass one anyway should read `COGNITO_IDENTITY_PROVIDER` instead of hardcoding `GoogleSAML`. There is no password form or sign-up. Only `@java.no` accounts get a token; anyone else is refused at sign-in.
- `external` — public users. Tokens carry only native Cognito groups of the external pool; registry groups are never added there, since anyone can sign up with any email.
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
| `COGNITO_DOMAIN` | Hosted-UI FQDN of the pool | e.g. `javabin-internal.auth.eu-central-1.amazoncognito.com` (external: `javabin-external.auth.eu-central-1.amazoncognito.com`) |
| `COGNITO_ISSUER_URL` | `https://cognito-idp.{region}.amazonaws.com/{pool_id}` | OIDC issuer for JWT validation |
| `COGNITO_WELL_KNOWN_URL` | `{issuer}/.well-known/openid-configuration` | OIDC discovery document |
| `COGNITO_CLIENT_ID` | SSM (ECS task `secrets`) | `confidential` and `public` only |
| `COGNITO_CLIENT_SECRET` | SSM SecureString (ECS task `secrets`) | `confidential` only |
| `COGNITO_IDENTITY_PROVIDER` | SSM (ECS task `secrets`) | `confidential` and `public` only. The IdP the client signs in through (`GoogleSAML`, `Google`, or `COGNITO`), for apps that send `identity_provider` themselves. Changes at the IdP cutover, which restarts the service |
| `COGNITO_GROUPS` | Comma-joined list from `auth.groups` | Only when `groups` is set |
| `COGNITO_EXTRA_AUTH_PARAMS` | Fixed per pool, URL query string | Internal pool, `confidential` and `public` only: `prompt=select_account`. Add it to your `/oauth2/authorize` request (see below) |

#### Account chooser (internal pool)

Send the parameters in `COGNITO_EXTRA_AUTH_PARAMS` (today `prompt=select_account`) **and** `identity_provider=<COGNITO_IDENTITY_PROVIDER>` with every sign-in redirect to `/oauth2/authorize`. With them, Google shows its account chooser, so a hero whose browser is signed in to a personal Google account can pick the `@java.no` one instead of being refused. Without them, Google silently uses the active account. Managed login drops `prompt` when the request does not name the IdP (it still redirects straight to it), so `prompt` alone is not enough. Read the IdP from `COGNITO_IDENTITY_PROVIDER`, never hardcode it: it changes at the IdP cutover.

- oidc-client-ts / nuxt-oidc-auth: `extraQueryParams: { prompt: 'select_account', identity_provider: process.env.COGNITO_IDENTITY_PROVIDER }`
- Spring Security: an `OAuth2AuthorizationRequestResolver` that adds `prompt=select_account` and `identity_provider` to `additionalParameters`
- NextAuth / Auth.js: `authorization: { params: { prompt: 'select_account', identity_provider: process.env.COGNITO_IDENTITY_PROVIDER } }`

Cognito forwards `prompt` only when the pool domain uses managed login; the classic hosted UI ignores it, so sending it is always safe. ALB sign-in (`routing.login`) sends both for you.

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

### calls / provides (app-to-app)

Apps can call each other privately inside the platform VPC, without going through the internet or
the public ALB. Both sides must agree: the **caller** lists the app it calls under `calls:`, and the
**callee** lists the caller under `provides.consumers:`. Either side removing its entry closes the
connection again.

```yaml
# Caller: cupcake-backend (team cupcake)
calls:
  - app: moresleep          # required
    team: moresleep         # optional, default: this app's team
    scopes: [talks.read]    # optional; omit for network-only
    env: SP_BASE_URL        # optional URL variable name, default MORESLEEP_URL
```

```yaml
# Callee: moresleep (team moresleep)
provides:
  scopes:                   # optional; scope: description (a list of names also works)
    talks.read: Read talks and speakers
    talks.write: Create and update talks
  consumers:                # who may call this app
    - app: cupcake-backend
      team: cupcake         # optional, default: this app's team
      scopes: [talks.read]  # optional; must be listed in provides.scopes
```

**What the platform does**

- Every app (with or without `calls`/`provides`) gets its own security group `{team}-{name}-tasks`
  and a private DNS name `{team}-{name}.javabin.internal` (Cloud Map). The ALB can reach only the
  app's `compute.port`; nothing else can, until a consumer is allowed.
- When both sides agree, the platform Lambda `javabin-app-peers-sync` opens the callee's port to the
  caller's security group, usually within a minute of the second apply.
- **Scopes** (optional) are OAuth2 client-credentials scopes in the internal Cognito pool. The callee
  gets a resource server `{team}-{name}`; each caller that requests scopes gets one machine-to-machine
  client. Granted scopes are what the caller requested **and** the callee allows for that caller.
- Routing is not needed on either side. An app with `provides:` and no `routing:` is reachable only
  from its consumers inside the VPC.

**Injected into the caller**

| Variable | Kind | Value |
|---|---|---|
| `<env>` (default `{APP}_URL`) | secret (SSM) | `http://{team}-{app}.javabin.internal:{port}` |
| `<env>` with `_URL` → `_SCOPES` (only with `scopes`) | secret (SSM) | Scopes actually **granted** for that callee, space-separated, e.g. `moresleep-moresleep/talks.read`; `none` until something is granted |
| `PEER_AUTH_TOKEN_URL` (any call with `scopes`) | env | Token endpoint of the internal pool |
| `PEER_AUTH_CLIENT_ID`, `PEER_AUTH_CLIENT_SECRET` (any call with `scopes`) | secrets | The caller's M2M client |

**Injected into the callee** (when `provides.scopes` is set): `PEER_AUTH_ISSUER`,
`PEER_AUTH_JWKS_URL`, `PEER_AUTH_SCOPE_PREFIX` (`{team}-{name}/`).

**Calling with scopes.** Request one token per callee, with that callee's scopes variable, and cache it
until it expires (60 minutes). Skip the request while the variable is `none`:

```bash
curl -s -u "$PEER_AUTH_CLIENT_ID:$PEER_AUTH_CLIENT_SECRET" \
  -d grant_type=client_credentials --data-urlencode "scope=$SP_BASE_SCOPES" "$PEER_AUTH_TOKEN_URL"
# -> {"access_token": "...", "expires_in": 3600}; send it as Authorization: Bearer <token>
```

Cognito refuses the whole token request (`invalid_scope`) if any requested scope is not granted, so
always request exactly the scopes variable, never a hard-coded list: it holds what the client really has.
When the callee grants fewer scopes (or more), the variable changes and the platform restarts the caller
so it picks up the new value. The callee
verifies the token's signature against `PEER_AUTH_JWKS_URL`, its `iss` against `PEER_AUTH_ISSUER`, and
checks that `scope` contains `${PEER_AUTH_SCOPE_PREFIX}<scope>` (Spring Security
`oauth2ResourceServer().jwt()`, `jose` in Node). One token per callee means a token can never be
replayed against another app.

**Status.** Each side can see the state of its edges:

```bash
aws ssm get-parameter --name /javabin/apps/<team>/<app>/peers-status --query Parameter.Value --output text
# calls: active | pending-consent | callee-missing | callee-invalid, with granted/denied scopes
# consumers: active | pending | caller-invalid
```

If an app's request becomes invalid (its `peers-status` shows `{"error": ...}`), the app is frozen rather
than removed: its existing rules, scopes and parameters stay, so a bad apply does not cut off its peers.
The other side can still withdraw while it is frozen. Removing the app (destroying its Terraform) removes
its edges for real.

**Security notes**

- A network-only call (no `scopes`) is **not caller authentication**. It only means "tasks with the
  caller's security group can reach the port". ECS attaches security groups and registers Cloud Map
  instances through its own service role, so a team that deliberately runs a task with another app's
  group ID would get that app's network identity. The daily audit reports this, but does not block it.
  Endpoints that change data or return anything sensitive should require scopes.
- The platform denies teams changing the private `javabin.internal` DNS zone, and the daily audit
  compares its records with Cloud Map. Cloud Map service names are not enforced at creation: a squatted
  `{your-team}-{app}` name makes your apply fail and is reported by the daily audit; ask the platform
  team to remove it.

**First deploy.** The caller's task definition references `peer-*-url`, `peer-*-scopes` and
`m2m-client-*`, which `javabin-app-peers-sync` writes shortly after the apply that adds the call. If the
Lambda has not written them (or failed), tasks fail to start with `ResourceInitializationError` and the
deployment rolls back. Check `peers-status` and the `#javabin-infra-alerts` channel, then re-run the
deploy.

**Rules and limits**

- Names: `app` as in `name:`, `team` as in `team:`. Scope names: lowercase letters, digits, `.`, `_`,
  `-`, max 40 characters. At most 20 calls, 50 consumers, 25 scopes.
- At most 50 scopes requested across all `calls` (one Cognito client holds at most 50). The whole
  request (`calls`, `provides.scopes` with descriptions, `consumers`) must fit the 4096-byte SSM
  parameter `network`; `expand` reports the size when it does not.
- `resources.secrets` cannot use the names the platform writes: `network`, `m2m-client-id`,
  `m2m-client-secret`, `peers-status`, `peer-*-url`, `peer-*-scopes`.
- The injected names must not also be set in `environment:`, a resource `env` or `auth.env`. Two calls
  to apps with the same name in different teams need an explicit `env:`.
- Traffic is plain HTTP inside the VPC. Already-issued tokens stay valid for up to 60 minutes after a
  scope is withdrawn, and open connections can outlive a removed rule until they close.
- At most about 25 apps can provide scopes (Cognito resource servers per pool).

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
3. `tf-plan` plans the committed files → LLM review → `tf-apply` on the default branch (main or master)
4. `docker-build` pushes `{team}-{name}` to ECR, `ecs-deploy` registers a task definition revision pinned to the pushed digest and rolls the service onto it (a new service is created with 0 tasks and gets `desired_count` here)

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
| Cognito client (`routing.login`) | `{team}-{name}.login-{N}`, credentials under `/javabin/apps/{team}/{name}/login-{N}/` |
| Task security group | `{team}-{name}-tasks` |
| Private DNS name | `{team}-{name}.javabin.internal` |
| SSM (app-to-app) | `/javabin/apps/{team}/{name}/network` (request), `peer-{t}-{a}-url`, `peer-{t}-{a}-scopes`, `m2m-client-{id,secret}`, `peers-status` |
| Cognito M2M | resource server `{team}-{name}`, client `m2m.{team}-{name}` (internal pool) |
