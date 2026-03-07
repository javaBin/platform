# Cognito + Google IdP Setup

Manual steps required alongside Terraform-managed infrastructure for Cognito user pools with Google Sign-In.

## Two Separate Google Integrations

This platform uses Google in two distinct ways. They require separate credentials and serve different purposes.

| Integration | Purpose | Credential Type | Stored In |
|------------|---------|----------------|-----------|
| Domain-wide delegation SA | Team provisioner manages Google Groups via Admin SDK | GCP Service Account JSON key | SSM `/javabin/platform/google-admin-sa` |
| OAuth 2.0 Client | User-facing Google Sign-In in Cognito pools | OAuth Client ID + Secret | Cognito IdP config (Terraform) |

The domain-wide delegation SA already exists in the `javabin-platform` GCP project. The OAuth client below is a separate credential that must be created.

## 1. Google Cloud Console: OAuth Client

### Prerequisites
- Access to `javabin-platform` GCP project (under `java.no` org)
- Permissions: `roles/oauthconfig.editor` or project Owner

### 1.1 Configure OAuth Consent Screen

1. Go to **APIs & Services > OAuth consent screen** in the `javabin-platform` project
2. Select **External** user type (needed for the external Cognito pool)
3. Fill in:
   - App name: `Javabin Platform`
   - User support email: `platform@java.no` (or board contact)
   - Developer contact: same
4. Scopes: add `openid`, `email`, `profile`
5. Test users: skip (not needed once published)
6. Publish the app (move from Testing to Production) — Google review may take a few days

### 1.2 Create OAuth 2.0 Client ID

1. Go to **APIs & Services > Credentials > Create Credentials > OAuth client ID**
2. Application type: **Web application**
3. Name: `javabin-cognito`
4. Authorized redirect URIs — add both Cognito pool callback URLs:
   ```
   https://auth-internal.javazone.no/oauth2/idpresponse
   https://auth-external.javazone.no/oauth2/idpresponse
   ```
   These are the Cognito hosted UI domains. Adjust if using custom domains.
5. Click **Create** and note the **Client ID** and **Client Secret**

These values are passed to Terraform as variables when creating the Cognito identity provider resources. They are not stored in SSM — Cognito holds them internally.

## 2. Cognito User Pools (Terraform-managed)

The `terraform/platform/identity/` module (not yet implemented) will create two Cognito user pools. Below is what Terraform will manage and what to verify manually.

### 2.1 Internal Pool (`javabin-internal`)

For Javabin heroes and board members — Google Workspace accounts only.

**Terraform creates:**
- User pool with email as primary attribute
- Google IdP with `hd=java.no` restriction (hosted domain claim)
- Hosted UI domain: `auth-internal.javazone.no`
- Default app client for platform admin UI

**Google IdP config (in Terraform):**
```hcl
resource "aws_cognito_identity_provider" "google_internal" {
  user_pool_id  = aws_cognito_user_pool.internal.id
  provider_name = "Google"
  provider_type = "Google"

  provider_details = {
    client_id                     = var.google_oauth_client_id
    client_secret                 = var.google_oauth_client_secret
    authorize_scopes              = "openid email profile"
    attributes_url                = "https://people.googleapis.com/v1/people/me?personFields="
    attributes_url_add_attributes = true
    authorize_url                 = "https://accounts.google.com/o/oauth2/v2/auth"
    oidc_issuer                   = "https://accounts.google.com"
    token_url                     = "https://oauth2.googleapis.com/token"
    token_request_method          = "POST"
  }

  attribute_mapping = {
    email    = "email"
    name     = "name"
    username = "sub"
  }
}
```

**Manual verification after `terraform apply`:**
1. Visit `https://auth-internal.javazone.no/login?client_id=<CLIENT_ID>&response_type=code&scope=openid+email+profile&redirect_uri=<CALLBACK>`
2. Confirm only `@java.no` accounts can sign in
3. Verify the `hd` claim filtering works (non-java.no accounts get rejected)

### 2.2 External Pool (`javabin-external`)

For public users — JavaZone attendees, community members.

**Terraform creates:**
- User pool with email as primary attribute
- Google IdP **without** `hd` restriction (any Google account)
- Self-registration enabled
- Hosted UI domain: `auth-external.javazone.no`

**Key differences from internal:**
- No hosted domain restriction on Google IdP
- Self-registration allowed
- Optional: GitHub social IdP (if configured)
- MFA optional (not enforced)

**Manual verification after `terraform apply`:**
1. Test sign-in with a non-java.no Google account
2. Test self-registration with email/password
3. Verify email verification flow works

## 3. App Client Registration

Apps declare their auth needs in `app.yaml`:

```yaml
auth: internal    # or: external, both, none
```

The `cognito-app-client` module (`terraform/modules/cognito-app-client/`) creates:
- Cognito user pool client(s) in the specified pool(s)
- SSM parameters with client credentials:
  - `/{project}/platform-apps/{name}/cognito-internal-client-id`
  - `/{project}/platform-apps/{name}/cognito-internal-client-secret`
  - `/{project}/platform-apps/{name}/cognito-external-client-id`
  - `/{project}/platform-apps/{name}/cognito-external-client-secret`

Apps read these SSM parameters at runtime — no secrets in env vars or code.

## 4. Checklist

### Before Terraform deployment
- [ ] OAuth consent screen configured and published in GCP
- [ ] OAuth 2.0 Client ID created with correct redirect URIs
- [ ] Client ID and Secret available for Terraform variables
- [ ] Domain-wide delegation SA already in SSM (separate from OAuth client)

### After Terraform deployment
- [ ] Internal pool: `@java.no` Google Sign-In works
- [ ] Internal pool: non-`@java.no` accounts are rejected
- [ ] External pool: any Google account can sign in
- [ ] External pool: self-registration works
- [ ] Hosted UI domains resolve correctly (Route53 + ACM)
- [ ] Test app client credentials in SSM are readable

### DNS records needed (created by identity module)
- `auth-internal.javazone.no` — CNAME to Cognito internal pool domain
- `auth-external.javazone.no` — CNAME to Cognito external pool domain
