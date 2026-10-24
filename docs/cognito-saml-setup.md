# Cognito SAML Setup — Google Workspace Federation

> **Superseded.** The internal pool moves to the Google OIDC provider (account chooser, Internal
> consent screen): see [cognito-google-setup.md](cognito-google-setup.md). This page describes the
> `GoogleSAML` provider that was created by hand and stays only until the cutover. The live provider maps
> `email` only — the groups attribute mapping described below was never configured; registry groups come
> from the auth-claims trigger.

## Overview

The internal Cognito pool authenticates Javabin heroes via Google Workspace SAML.
Users sign in with their `@java.no` account. Google sends group membership in the
SAML assertion, which Cognito maps to token claims. Apps read groups from the JWT
for authorization (e.g., `pkom-admin` gets elevated access).

This is separate from the Identity Center SAML app (which handles AWS console access).
Same pattern, different audience.

## Prerequisites

- Google Workspace admin access (admin.google.com)
- The Cognito internal pool exists: `javabin-internal` (`eu-central-1_Icikv3dtD`)
- Terraform applied with SAML provider configuration (step 3 below)

## Step 1: Get Cognito SAML Endpoints

From the Cognito console or CLI:

```bash
# Pool ID
aws cognito-idp describe-user-pool \
  --user-pool-id eu-central-1_Icikv3dtD \
  --profile javabin --region eu-central-1 \
  --query 'UserPool.{Domain:Domain,Id:Id}'
```

You need:
- **ACS URL**: `https://<cognito-domain>.auth.eu-central-1.amazoncognito.com/saml2/idpresponse`
- **Entity ID (SP)**: `urn:amazon:cognito:sp:eu-central-1_Icikv3dtD`

If a custom domain is not yet configured, the domain will be set in step 3.

## Step 2: Create Google SAML App

1. Go to **admin.google.com** → **Apps** → **Web and mobile apps** → **Add app** → **Add custom SAML app**

2. Name: `Javabin Apps (Cognito)`

3. On the Google IdP information page, download the **IdP metadata XML** file. You'll need this for Terraform.

4. Configure the Service Provider:
   - **ACS URL**: `https://javabin-internal.auth.eu-central-1.amazoncognito.com/saml2/idpresponse`
     (or your custom domain if configured)
   - **Entity ID**: `urn:amazon:cognito:sp:eu-central-1_Icikv3dtD`
   - **Name ID format**: `EMAIL`
   - **Name ID**: `Basic Information > Primary email`

5. Configure Attribute Mapping:

   | Google Directory attribute | App attribute |
   |---------------------------|---------------|
   | Primary email | email |
   | First name | firstName |
   | Last name | lastName |

6. Configure Group Membership (this is key):
   - Click **Add group membership**
   - Select the groups you want sent in the assertion:
     - `helter@java.no`
     - `styret@java.no`
     - `drift@java.no`
     - `pkom@java.no`
     - `javabin-developer@java.no`
     - (any future groups from groups.yaml)
   - **App attribute for groups**: `groups`

7. Click **Finish**, then turn the app **ON** for the organizational units that should have access (typically the entire `java.no` org).

## Step 3: Configure Cognito in Terraform

Upload the IdP metadata XML to SSM:

```bash
aws ssm put-parameter \
  --name /javabin/platform/google-saml-metadata \
  --type String \
  --value "$(cat GoogleIDPMetadata.xml)" \
  --profile javabin --region eu-central-1
```

Then add the SAML provider to the Cognito identity module. The Terraform
configuration needs:

```hcl
resource "aws_cognito_identity_provider" "internal_google_saml" {
  user_pool_id  = aws_cognito_user_pool.internal.id
  provider_name = "GoogleSAML"
  provider_type = "SAML"

  provider_details = {
    MetadataURL = "https://..."  # or MetadataFile with the XML content
    IDPSignout  = "true"
  }

  attribute_mapping = {
    email                  = "email"
    given_name             = "firstName"
    family_name            = "lastName"
    "custom:groups"        = "groups"
  }
}
```

Note: The `groups` attribute from SAML needs to be mapped to a custom attribute
(`custom:groups`) since Cognito doesn't natively map SAML groups to `cognito:groups`.
A pre-token generation Lambda trigger then copies `custom:groups` into the token's
`cognito:groups` claim.

## Step 4: Pre-Token Generation Lambda (if needed)

If the SAML `groups` attribute maps to `custom:groups`, you need a Lambda trigger
on the Cognito pool to copy it into the `cognito:groups` token claim:

```python
def handler(event, context):
    groups_str = event['request']['userAttributes'].get('custom:groups', '')
    if groups_str:
        groups = [g.strip() for g in groups_str.split(',')]
        event['response']['claimsOverrideDetails'] = {
            'groupOverrideDetails': {
                'groupsToOverride': groups,
            }
        }
    return event
```

This trigger runs at sign-in time. The user's Google groups appear in the
`cognito:groups` claim of the ID token. Apps read this claim for authorization.

## Step 5: Configure Cognito Domain

A domain is required for the hosted UI / SAML endpoints:

```bash
# Option A: Cognito-provided domain
aws cognito-idp create-user-pool-domain \
  --user-pool-id eu-central-1_Icikv3dtD \
  --domain javabin-internal \
  --profile javabin --region eu-central-1

# Option B: Custom domain (requires ACM cert in us-east-1)
# Configured via Terraform — see identity module
```

## Step 6: Create App Clients

Each app that needs authentication gets a Cognito app client:

```bash
aws cognito-idp create-user-pool-client \
  --user-pool-id eu-central-1_Icikv3dtD \
  --client-name "my-app" \
  --supported-identity-providers GoogleSAML \
  --allowed-o-auth-flows code \
  --allowed-o-auth-scopes openid email profile \
  --callback-urls "https://my-app.javazone.no/callback" \
  --logout-urls "https://my-app.javazone.no" \
  --profile javabin --region eu-central-1
```

Or via the `cognito-app-client` Terraform module.

## How Apps Use Groups

The ID token from Cognito contains:

```json
{
  "sub": "...",
  "email": "alexander.amiri@java.no",
  "cognito:groups": ["helter", "drift", "pkom"],
  ...
}
```

Apps check group membership for authorization:

```python
# Example: Python/FastAPI
groups = token_claims.get("cognito:groups", [])
if "pkom-admin" in groups:
    # elevated access
```

## Relationship to Identity Center

| | Identity Center | Cognito |
|---|---|---|
| Purpose | AWS console access | App authentication |
| Google SAML app | Separate app | Separate app |
| Who gets access | drift, styret, developers | All heroes |
| Groups carry | Team attribute (ABAC) | All groups (app auth) |
| Managed by | Platform Terraform (org/) | Platform Terraform (identity/) |

Both use Google SAML with group claims, but they serve different audiences
and have different group→access mappings.
