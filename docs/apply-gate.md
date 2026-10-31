# Apply Gate — Credential-Brokered Terraform Apply

## Overview

Terraform apply does NOT have direct IAM permissions to modify infrastructure.
Instead, a Lambda (`javabin-apply-gate`) acts as a credential broker: it verifies
the risk assessment and override status before issuing short-lived STS credentials.

## Why

If tf-apply had direct IAM permissions, the risk gate would be a shell `if`
statement — the credentials would already be available. A compromised or
modified workflow step could skip the check. The gate Lambda ensures credentials
are only issued after verification.

## Flow

```
tf-plan     → uploads plan + plan-output.txt to S3
plan-review → LLM reviews plan → uploads risk.json to S3
tf-apply    → assumes javabin-ci-apply-gate (lightweight OIDC role)
            → downloads plan from S3, verifies SHA256
            → invokes gate Lambda: check(plan_key, team, id_token)
            → Lambda verifies the GitHub OIDC token (aud javabin-apply-gate,
              a javaBin/platform workflow on main) and takes the repo from its
              signed `repository` claim; plan_key must be under plans/javaBin/<repo>/
            → Lambda reads risk.json from S3
              → LOW/MEDIUM: assumes app CI role via STS, returns temp credentials
              → HIGH: checks for override.json, verifies HMAC signature
                → valid override: issues credentials
                → no override: returns 403, apply fails
            → tf-apply uses temp credentials for terraform init + apply
```

## Caller identity

The gate and broker roles can be assumed from any javaBin repo's platform
workflow job, and code inside that job (e.g. a Terraform `external` data
source) can invoke the Lambdas directly. So nothing identity-related is taken
from the payload:

- `check` — repo comes from the job's GitHub OIDC token, the team must be the
  app.yaml team the repo belongs to, and the plan must be the repo's own.
- `sign` — only a token from `approve-override.yml` in javaBin/platform,
  running in the board-reviewed `override-approval` environment, is accepted.
  The recorded approver is the token's `actor`: whoever dispatched the workflow (a board member must still approve the environment for it to run). Without this, any app's tf-apply job
  could sign its own HIGH-risk override.

Tokens are minted with `scripts/github-oidc-token.sh <audience>` and verified
in `terraform/lambda-src/shared/github_oidc.py` (RS256 against GitHub's JWKS).

Plan credentials are read-only. The broker vends `javabin-ci-plan-{team}`
(ReadOnlyAccess, read on the team's state, the state lock, and PutObject on
`plans/*`) to tf-plan, which runs on any branch. The only way to get the
write-capable `javabin-ci-team-{team}` is through this gate, for a reviewed
plan.

State access is limited by an explicit Deny, not by the Allow. `ReadOnlyAccess`
(plan) and `AllowAll` (team) cover every key in the state bucket, and the
developer boundary's `DenyXTeam` exempts that bucket, because S3 objects carry
no team tag. Both roles therefore carry `DenyForeignTerraformState`, which
denies the platform's state (`infra/`, `org/`, `state/`, `env:/`) and every
other registered team's `apps/{team}/` prefix. The list is built from
`registered_teams`, so a new top-level prefix in the bucket must be added to
`local.foreign_state_keys` in `terraform/platform/iam/main.tf`.

## IAM Roles

| Role | Purpose | Permissions |
|------|---------|-------------|
| `javabin-ci-apply-gate` | OIDC role for tf-apply workflow | `lambda:InvokeFunction` on gate Lambda + `s3:GetObject` on plan bucket |
| `javabin-apply-gate` | Lambda execution role | `ssm:GetParameter` (signing key) + `s3:Get/PutObject` (plan bucket) + `sts:AssumeRole` (app CI roles) |
| `javabin-ci-plan-{team}` | Read-only plan role | Vended by the CI broker for tf-plan: ReadOnlyAccess + own state read (other keys denied) + lock + plan upload |
| `javabin-ci-team-{team}` | Team infrastructure role (write) | Vended by the gate Lambda for a reviewed plan (apply); other teams' and platform state denied |

## Credential Lifetime

- Temp credentials issued by the gate Lambda have a **15-minute TTL** (`CREDENTIAL_DURATION=900`)
- This is enough for `terraform init + apply` on most plans
- Credentials are scoped to the specific app's CI role (`javabin-ci-app-{repo}`)

## Risk Levels

| Level | Action |
|-------|--------|
| LOW | Auto-approve, credentials issued |
| MEDIUM | Auto-approve, credentials issued |
| HIGH | Blocked until override token exists and HMAC is valid |
| FAILED | Blocked: the review service gave no verdict (Bedrock or Lambda failure). Not a risk finding; re-run all jobs or override |

## Override Tokens

An override token is an HMAC-signed JSON file stored in S3 alongside the plan:

```json
{
  "signature": "HMAC-SHA256(signing_key, plan_key)",
  "plan_key": "javaBin/moresleep/abc123/tfplan",
  "approved_by": "alexanderamiri",
  "reason": "Reviewed manually, safe to apply",
  "approved_at": "2026-03-10T12:00:00Z"
}
```

- **Signing key**: Random 256-bit secret in SSM at `/javabin/platform/override-signing-key`
- **HMAC is plan-scoped**: `HMAC(key, plan_key)` — different plan = different signature, can't reuse
- **Only the Lambda** can read the signing key — no CI role has SSM access to it
- **Tokens expire** with the S3 lifecycle on the plan bucket

## Override Flow

1. Apply blocks on HIGH or FAILED → Terraform Apply fails with what to do
   (see below), and a Slack alert with the plan key, run link and override link
   goes to the override channel
2. A platform team member dispatches `approve-override.yml` in the platform repo
   with the plan key and a reason
   - Protected by GitHub environment `override-approval` (requires review by the
     `javaBin/platform` team)
   - The repo and run are read from the plan key
     (`plans/javaBin/<repo>/<run_id>/tfplan`); the optional `repo` and `run_id`
     inputs only have to match it. A malformed key fails before anything is signed
3. Override workflow assumes `javabin-ci-override-approver` OIDC role
4. Invokes gate Lambda `sign` action → Lambda computes HMAC, writes `override.json` to S3
5. The blocked run is re-run:
   - javaBin/platform runs: the workflow runs `gh run rerun --failed` itself
   - app repos: the workflow's `github.token` only covers javaBin/platform (no
     platform GitHub App has `actions: write`), so the job summary and Slack
     message give the run URL and the developer clicks **Re-run failed jobs**
6. Apply re-runs → gate Lambda finds `override.json` → verifies HMAC → issues
   credentials. The gate checks for an override on every run, so no other state
   has to change. Docker build and ECS deploy, which wait for a successful
   apply, run after it.

## Requesting an override

A HIGH or FAILED review blocks Terraform Apply, and with it the image build
and ECS deploy of the same run. The Terraform Apply job prints which case it
is, and the plan key.

**HIGH — the review found HIGH risk.** Read the review in the Terraform Plan
job log or the PR comment. If the change is intended:

1. Ask the platform team (GitHub team `javaBin/platform`, or the override Slack
   channel where the alert was posted) for an override. Send the plan key
   (`plans/javaBin/<repo>/<run_id>/tfplan`), the run URL, and why the change is
   safe.
2. They run [approve-override.yml](https://github.com/javaBin/platform/actions/workflows/approve-override.yml)
   with that plan key and a reason, or:

   ```bash
   gh workflow run approve-override.yml -R javaBin/platform \
     -f plan_key=plans/javaBin/<repo>/<run_id>/tfplan -f reason="..."
   ```

   A `javaBin/platform` team member must then approve the `override-approval`
   environment.
3. When it has finished, open your blocked run and click **Re-run failed jobs**.

**FAILED — the review service failed.** This is not a risk finding: Bedrock
or the plan-review Lambda did not return a verdict. `invoke-plan-review.sh`
already retries (3 invokes by default, each with 3 Bedrock attempts) before
giving up. Click **Re-run all jobs** on the run later; that re-plans and
re-reviews. **Re-run failed jobs** does not help, since it re-reads the same
FAILED verdict. If the service stays down, request an override as for HIGH,
then click **Re-run failed jobs**.

The override is for that plan key (HMAC over the key) and expires with the
plan bucket's lifecycle. A push of a new commit makes a new run and plan key,
which is reviewed afresh. Known gap: **Re-run all jobs** re-plans the same
commit under the same key (the key holds the run ID, not the attempt), and an
`override.json` signed for an earlier attempt is still accepted.

## S3 Plan Artifact Layout

```
{repo}/{sha}/
  tfplan              # binary plan file
  plan-output.txt     # human-readable plan text
  risk.json           # {"level": "HIGH", "reviewed_at": "..."}
  override.json       # HMAC-signed override token (only if overridden)
```

## SSM Parameters

| Path | Type | Purpose |
|------|------|---------|
| `/javabin/platform/override-signing-key` | SecureString | HMAC signing key for override tokens |

## Setup

The signing key must be created once:

```bash
aws ssm put-parameter \
  --name /javabin/platform/override-signing-key \
  --type SecureString \
  --value "$(openssl rand -hex 32)" \
  --profile javabin --region eu-central-1
```

The `override-approval` GitHub environment must be created on the platform repo
with required reviewers set to board members.
