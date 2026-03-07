# Phase 1 — Identity (Google Workspace → AWS)

## Agent Brief

You are setting up identity federation so that Javabin heroes with `@java.no` Google Workspace
accounts can access AWS via IAM Identity Center. Existing IAM users are left completely
untouched — they continue working in parallel throughout.

**Prerequisites:** Phase 0 is complete. State backend exists. Organizations is enabled.
Permission boundary policy exists.

**Repo:** `javaBin/platform`
**AWS Region:** eu-west-1 (or whatever discovery confirmed)
**Domain:** java.no
**GitHub Org:** javaBin

---

## Context: What Javabin Is

Non-profit Java user group. ~70 active heroes. Google Workspace non-profit with @java.no emails.
Board-governed. Heroes get free conference tickets and elevated access to tools.

## Context: Google Workspace State

- Active non-profit Google Workspace (free Business Starter licenses)
- Used for @java.no emails (faktura@java.no etc) and Google Groups
- Alexander has Google Workspace admin access

---

## Task 1: Google Workspace Setup (Document Steps — Alexander Executes Manually)

Create a runbook document at `docs/google-workspace-setup.md` with precise steps for Alexander:

### Hero Account Creation
1. Bulk create hero accounts via CSV import in Google Admin Console (or GAM CLI)
   - Format: `firstname.lastname@java.no`
   - Send welcome email with one-time password reset link (built into Workspace bulk create)
   - Heroes set their own passwords and enroll their own 2FA
2. Include a CSV template for bulk import

### Google Workspace Groups to Create
| Group | Email | Members | Purpose |
|-------|-------|---------|---------|
| Heroes | `heroes@java.no` | All active heroes (~70) | General hero access |
| Board | `board@java.no` | Board members | Governance, org-level approvals |
| Program Committee | `pkom@java.no` | Program committee members | CFP review access |
| Infrastructure | `infra@java.no` | People with elevated AWS access | Platform administration |
| JavaZone Crew | `javazone-crew@java.no` | All JavaZone organizers | Conference operations |

### 2FA Policy
- Set to "Optional" initially with a grace period
- Enforce later when everyone has onboarded
- Does NOT affect AWS IAM users at all

### Google SAML App for AWS Identity Center
Detailed steps for configuring Google Workspace as external SAML IdP:
1. In Google Admin → Apps → Web and mobile apps → Add custom SAML app
2. Name: "AWS IAM Identity Center"
3. Service provider entity ID: (from Identity Center SAML metadata)
4. ACS URL: (from Identity Center)
5. Configure attribute mappings:
   - `Subject` → `Basic Information > Primary email`
   - `https://aws.amazon.com/SAML/Attributes/groups` → Google Groups membership
6. Assign to "Everyone" or specific OUs

---

## Task 2: IAM Identity Center (`terraform/platform/identity/`)

Create the identity module in the platform Terraform.

**Resources to create:**

### Identity Center Instance
```hcl
resource "aws_ssoadmin_instance" "main" {
  # AWS auto-creates this when you enable Identity Center
  # Use data source if already enabled, resource if not
}
```

### SAML Identity Provider
Configure Google Workspace as the external SAML IdP for Identity Center.
The SAML metadata XML will need to be downloaded from Google Admin and either:
- Stored as a file in the repo (it's not secret — it's public metadata)
- Or referenced via URL

### Permission Sets

**JavabinAdmin:**
- Managed policy: `AdministratorAccess`
- Assigned to: `infra@java.no` group
- No permission boundary needed (admin is trusted)
- Session duration: 4 hours

**JavabinDeveloper:**
- Custom inline policy with team-scoped read access:
  ```json
  {
    "Effect": "Allow",
    "Action": ["*:Describe*", "*:Get*", "*:List*", "ce:GetCostAndUsage"],
    "Resource": "*",
    "Condition": {
      "StringEqualsIfExists": {
        "aws:ResourceTag/team": "${aws:PrincipalTag/team}"
      }
    }
  }
  ```
- Plus team-scoped SSM read:
  ```json
  {
    "Effect": "Allow",
    "Action": ["ssm:GetParameter", "ssm:GetParameters", "ssm:GetParametersByPath"],
    "Resource": "arn:aws:ssm:*:*:parameter/javabin/apps/*",
    "Condition": {
      "StringEquals": {
        "aws:ResourceTag/team": "${aws:PrincipalTag/team}"
      }
    }
  }
  ```
- Assigned to: `heroes@java.no` group
- Team tag derived from Google group membership (via SAML attribute)

**JavabinReadOnly:**
- Managed policy: `ViewOnlyAccess`
- Unconditioned — can see all resources (governance visibility)
- Assigned to: `board@java.no` and `pkom@java.no` groups
- Session duration: 4 hours

### Group-to-PermissionSet Assignments
Map Google Workspace groups to permission sets on the AWS account:
- `infra@java.no` → JavabinAdmin
- `heroes@java.no` → JavabinDeveloper
- `board@java.no` → JavabinReadOnly
- `pkom@java.no` → JavabinReadOnly

### Principal Tags
Configure Identity Center to pass the `team` tag as a principal tag on sessions.
The team tag comes from the hero's Google group membership:
- Heroes in `team-core@java.no` → `team=core`
- Heroes in `team-nettside@java.no` → `team=nettside`
- etc.

---

## Task 3: Migration Runbook

Create `docs/iam-user-migration.md` — steps to migrate existing IAM users to Identity Center:

1. Hero receives their @java.no Google account (from Task 1)
2. Hero logs into AWS via Identity Center URL (confirm access works)
3. Hero confirms they can see the resources they need
4. Admin disables the hero's IAM user access key (does NOT delete yet)
5. After 2 weeks with no issues, admin deletes the IAM user
6. Update discovery document to track migration status

**Important:** This is gradual. Do NOT batch-disable IAM users. One at a time, with
confirmation from each hero that Identity Center works for them.

---

## Constraints

- **Do NOT touch existing IAM admin users.** They continue working as before.
- **Do NOT enforce 2FA org-wide on Google.** Start optional, enforce later.
- **Identity Center module goes in `terraform/platform/identity/`** — it's CI-applied
  (unlike the org module which is human-applied).
- **SAML metadata from Google is not secret.** It can live in the repo.
- **Test with Alexander's account first** before bulk-creating hero accounts.

## Deliverables

1. `docs/google-workspace-setup.md` — runbook for Alexander
2. `docs/iam-user-migration.md` — per-user migration runbook
3. `terraform/platform/identity/` — Identity Center module:
   - `main.tf` — SAML provider, permission sets, group assignments
   - `variables.tf` — group names, session durations
   - `outputs.tf` — Identity Center instance ARN, permission set ARNs

## What Comes Next (DO NOT implement)

Phase 2 builds the platform infrastructure (VPC, ALB, OIDC CI roles, ECS, monitoring, Cognito).
Phase 3 builds the CI golden path and registry.
