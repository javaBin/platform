# Cognito + Google Sign-In Setup

How the internal Cognito pool (`javabin-internal`, heroes with `@java.no` accounts) signs users in with
Google, and how to cut over from the `GoogleSAML` provider. Background: javaBin/platform#157 (personal
Gmail accounts hit a dead-end `403 app_not_configured_for_user` page on the SAML app, with no way to
pick another account).

## Why OIDC, not SAML

Google's SAML IdP always answers for the browser's *active* Google session and has no account chooser.
The Google provider can: with `prompt=select_account` Google shows its account chooser, and the
OAuth client lives in a GCP project with an **Internal** consent screen, so Google itself refuses
non-java.no accounts on a page that lets the user switch account. Getting `prompt` to Google needs
managed login on the pool domain (step 3).

| Integration | Purpose | Credential | Stored in |
|---|---|---|---|
| Domain-wide delegation SA | team-provisioner manages Google Groups (Admin SDK) | Service account JSON key | SSM `/javabin/platform/google-admin-sa` |
| Internal-pool OAuth client | Google sign-in for the internal pool | OAuth client ID + secret | SSM `/javabin/platform/google-oauth/internal/{client-id,client-secret}` |
| External-pool OAuth client (later) | Google sign-in for the external pool | OAuth client ID + secret | not set up yet (`google_client_id` tfvar) |

The internal client must be separate from any external-pool client: its consent screen is Internal,
the external one would be External.

## 1. GCP: create the OAuth client (one-time, GCP admin)

Project: `javabin-platform` (under the `java.no` organisation). Needs `roles/oauthconfig.editor` or Owner.

1. **APIs & Services → OAuth consent screen** (Google Auth Platform → Branding/Audience):
   - User type / Audience: **Internal**. Only `java.no` Workspace accounts can complete sign-in; Google
     shows other accounts an error page *with* the account switcher.
   - App name `Javabin`, support email and developer contact `platform@java.no` (or a board address).
   - Scopes (Data access): `openid`, `.../auth/userinfo.email`, `.../auth/userinfo.profile`.
   - Internal apps need no Google verification or publishing.
2. **APIs & Services → Credentials → Create credentials → OAuth client ID**:
   - Application type: **Web application**, name `cognito-javabin-internal`.
   - Authorized JavaScript origins: none.
   - Authorized redirect URI — exactly one, the pool's Cognito domain:
     ```
     https://javabin-internal.auth.eu-central-1.amazoncognito.com/oauth2/idpresponse
     ```
     (`terraform output` → `internal_user_pool_domain` + `/oauth2/idpresponse`. There is no
     `auth-internal.javazone.no` domain; an earlier version of this doc listed it by mistake.)
   - Create, and copy the client ID and secret.

## 2. AWS: store the client in SSM (platform admin)

```bash
aws ssm put-parameter --profile javabin --region eu-central-1 \
  --name /javabin/platform/google-oauth/internal/client-id --type String \
  --value '<client-id>.apps.googleusercontent.com'
aws ssm put-parameter --profile javabin --region eu-central-1 \
  --name /javabin/platform/google-oauth/internal/client-secret --type SecureString \
  --value '<client-secret>'
```

Then re-run platform CI (any merge, or re-run the latest `main` workflow). The plan reads the path with
`aws_ssm_parameters_by_path` and creates `aws_cognito_identity_provider.internal_google` (`Google`)
once both parameters exist. Until then the plan creates nothing, which is why the Terraform could merge
first.

Adding the IdP changes **no app client**: clients use only the IdPs in `internal_identity_providers`
(`terraform/platform/identity/variables.tf`, default `["GoogleSAML"]` until the cutover in step 4). GoogleSAML keeps
working.

What Terraform configures (`terraform/platform/identity/main.tf`):

- `authorize_scopes = "openid email profile"`
- `authorize_url = https://accounts.google.com/o/oauth2/v2/auth`, plain: Cognito drops query
  parameters from a Google-type `authorize_url` (`describe-identity-provider` shows the URL without
  them, and every plan showed a diff), so `prompt` has to come from the app (step 3)
- attribute mapping `email`, `email_verified`, `name`, `username = sub`

## 3. Account chooser: managed login + `prompt=select_account`

Cognito drops query parameters from the provider's `authorize_url`, and the classic hosted UI
(`ManagedLoginVersion` 1) ignores a `prompt` parameter sent by the app. Managed login (version 2)
forwards `prompt` (except `none`) to the IdP, **but only when the request names it with
`identity_provider=`**: on the single-IdP auto-redirect it drops `prompt` (probe, 2026-10-30). So:

| Piece | Where | State |
|---|---|---|
| A managed-login branding style per app client (Cognito defaults) | `app-auth-sync` (created with the client, backfilled by reconcile, deleted with the client) | automatic |
| Apps send `prompt=select_account` and `identity_provider=$COGNITO_IDENTITY_PROVIDER` to `/oauth2/authorize` | `COGNITO_EXTRA_AUTH_PARAMS` (generate-modules.py); SPA `extraQueryParams` etc. — see app-yaml-reference "Account chooser" | per app |
| ALB sign-in (`routing.login`) sends both (the IdP read from the client's `cognito-client-identity-provider`) | `authentication_request_extra_params` in `terraform/modules/service-routing` | automatic, internal pool |
| Internal pool domain on managed login | `internal_managed_login_version = 2` in `terraform/platform/identity/variables.tf` (an in-place update of the domain; 1 = back to the classic UI) | done |

The external pool's domains stay on the classic hosted UI. Nothing there needs `prompt`, and its
clients get branding styles too, so it can move later with the same one-line change.

**Order matters.** On a managed-login domain a client without a branding style has no working
sign-in page. The style is harmless on the classic UI, so styles go first:

1. Merge the app-auth-sync change. Platform CI queues a reconcile after the apply, which gives every
   existing client (including `login-N` slot clients) a style. Check every internal client:
   ```bash
   POOL=$(aws cognito-idp list-user-pools --max-results 60 \
     --query "UserPools[?Name=='javabin-internal'].Id" --output text)
   for c in $(aws cognito-idp list-user-pool-clients --user-pool-id $POOL --max-results 60 \
       --query 'UserPoolClients[].ClientId' --output text); do
     aws cognito-idp describe-managed-login-branding-by-client --user-pool-id $POOL --client-id $c \
       --query ManagedLoginBranding.ManagedLoginBrandingId --output text >/dev/null 2>&1 \
       && echo "$c ok" || echo "$c MISSING STYLE"
   done
   ```
   Clients made outside app-auth-sync (e.g. app-peers-sync's `m2m.*` client-credentials clients) use
   no sign-in page and need no style.
2. PR: `internal_managed_login_version = 2`. The plan must show an in-place update of
   `aws_cognito_user_pool_domain.internal` only (no pool, domain or client replacement).
3. After apply: open an app's sign-in (or run the probe below) and confirm the managed-login page or a
   straight redirect to the IdP, not an error page.

Probe: the platform keeps a probe client in the internal pool, `platform-idp-probe`, that lists only
`internal_idp_probe_identity_providers` (identity module, default `["Google"]`; created once the
Google IdP exists). It is an ordinary app-auth-sync request written by the identity module
(`/javabin/apps/platform/idp-probe/auth`; only `platform` requests may set `identity_providers`), so it
gets a managed-login branding style like every app client. It cannot sign anyone in: public client,
callback `https://localhost/idp-probe`, and allowed group `idp-probe-nobody`, which does not exist, so
auth-claims refuses every token (its auth-status lists that group under `unknown_groups`, as expected).

The probe is an unauthenticated GET; it only reads the redirect Cognito returns (the client ID is not
secret):

```bash
DOMAIN=javabin-internal.auth.eu-central-1.amazoncognito.com
CID=$(aws ssm get-parameter --name /javabin/apps/platform/idp-probe/cognito-client-id --query Parameter.Value --output text)
Q="response_type=code&client_id=$CID&redirect_uri=https://localhost/idp-probe&scope=openid+email"
for extra in "&prompt=select_account" "&prompt=select_account&identity_provider=Google" ""; do
  loc=$(curl -s -o /dev/null -w '%{redirect_url}' "https://$DOMAIN/oauth2/authorize?$Q$extra")
  echo "${extra:-<no prompt>}: host=$(echo "$loc" | cut -d/ -f3)" \
    "prompt=$(echo "$loc" | grep -o 'prompt=[^&]*' || echo -)" "hd=$(echo "$loc" | grep -o 'hd=[^&]*' || echo -)"
done
```

Result (2026-10-30, managed login, Google-type IdP): all three go straight to `accounts.google.com`,
but only the second carries `prompt=select_account`; with `prompt` alone (no `identity_provider`) and
with nothing, Google gets no `prompt`. `hd=` never appears (Cognito drops it, also when the app sends
it); the Internal consent screen and auth-claims enforce the domain instead. To probe another IdP (e.g. a replacement OIDC provider),
point `internal_idp_probe_identity_providers` at it and use that name in `identity_provider=`.
For app clients, `scripts/auth-doctor.py` sends the pool's `COGNITO_EXTRA_AUTH_PARAMS` plus
`identity_provider` (as apps must) and warns when Google is not asked for the account chooser.
Since `prompt` does reach Google once the IdP is named, no `OIDC`-type replacement provider is needed.

## 4. Cutover: GoogleSAML → Google

Prerequisites:

- The Google IdP exists (step 2), the internal domain is on managed login and the probe passes
  (step 3).
- **Hard requirement:** server-side domain enforcement is live — the internal-pool lockdown (#166:
  auth-claims `REQUIRED_EMAIL_DOMAIN`, no self sign-up, no `COGNITO` on internal clients). The Internal
  consent screen already blocks other Google accounts, but the pool must not rely on one GCP setting.
  This branch builds on that change; the identity module also refuses `Google` in the list while
  `google_workspace_domain` is empty, and auth-claims requires `email_verified=true` from Google users
  (Google is never trusted on the IdP name alone).

Steps:

1. PR: `internal_identity_providers = ["Google"]` (identity `variables.tf` default). The same list feeds
   app clients and auth-claims' `FEDERATED_IDENTITY_PROVIDERS`, so one change moves both. The plan fails
   on an output precondition if the Google credentials are not in SSM. After apply, platform CI queues an
   app-auth-sync reconcile: every internal client switches to `Google` only, and
   `cognito-client-identity-provider` changes to `Google`, which restarts each app (app-secret-rollout)
   so `COGNITO_IDENTITY_PROVIDER` is current.
2. Check: `python3 scripts/auth-doctor.py --app-yaml <app>/app.yaml` for each auth app (cupcake), or the
   doctor step in the app's next deploy. Expect `authorize … redirects to Google (accounts.google.com)`.
3. Sign in once yourself with a personal Gmail active in the browser: Google must show the account chooser.

### Users after cutover

Google users are new Cognito users: username `Google_<google sub>`, a **new `sub`**. Today the pool has
4 users, all `GoogleSAML_<email>` (external-provider users; there are no native users to link to —
Cognito can only link a federated identity onto a native user).

- The platform keys everything on **email**, not `sub`: registry group membership (`group#<name>`
  members) and the auth-claims lookup use the email claim; per-client allowed groups are keyed by client
  ID. Nothing in the platform needs migrating.
- Apps that stored data keyed by `sub` (check cupcake/frosting) must re-key by email or map old to new.
- Delete the 4 `GoogleSAML_*` users a week after cutover (`aws cognito-idp admin-delete-user`); they are
  harmless meanwhile. If a Google sign-in ever fails because the email already exists, delete that
  user's `GoogleSAML_*` entry first.

### Decommission GoogleSAML (a week later)

1. Delete the `GoogleSAML` provider from the pool (created outside Terraform; nothing in Terraform
   references it). app-auth-sync errors if a pool's declared list still names it.
2. Turn off the Google Admin SAML app "Javabin Apps (Cognito)".
3. Mark `docs/cognito-saml-setup.md` for removal and drop the CLAUDE.md known issue.

## External pool

The external pool (`javabin-external`) still uses the `google_client_id` / `google_client_secret`
variables (empty, so no Google IdP) and `external_identity_providers = ["COGNITO"]`. Setting it up needs
a separate OAuth client with an **External** consent screen (Google verification for public use) and a
redirect URI on the external pool's domain.
