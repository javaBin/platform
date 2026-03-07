# Platform CI Workflow

Defined in `.github/workflows/platform-ci.yml`. Runs on pushes to `terraform/platform/**`, `terraform/lambda-src/**`, and `scripts/**`.

## Pipeline

```
push to main ──► plan ──► review ──► apply
PR             ──► plan ──► review ──► PR comments (no apply)
Monday 06:00   ──► drift detection ──► Slack if drift found
```

## Jobs

### plan
- OIDC → `javabin-ci-infra` role
- `terraform init`, `validate`, `fmt -check`, `plan`
- Uploads `tfplan` + `plan-output.txt` to S3 with SHA256 hash
- Posts plan output as PR comment on pull requests
- Outputs: `has_changes`, `plan_key`, `plan_sha256`

### review
- Downloads plan text from S3 (does NOT re-run terraform plan)
- Runs `scripts/review-plan.py` which calls Bedrock (Claude Haiku) for risk analysis
- Structured output via Bedrock Converse tool use: `{risk: LOW|MEDIUM|HIGH, summary: ..., findings: [...]}`
- Posts review as PR comment
- HIGH risk on main → sends Slack notification
- Output: `risk_level`

### apply
- Only on `push` to `main` with changes
- Checks risk level: blocks on HIGH/FAILED/UNKNOWN
- Downloads plan from S3, verifies SHA256 integrity
- `terraform apply -auto-approve tfplan`
- Uses `environment: production` (GitHub environment protection)

### drift
- Weekly schedule (Monday 06:00 UTC)
- Runs `terraform plan -detailed-exitcode`
- If drift detected, posts to Slack with plan output

## Risk Gate

- **LOW/MEDIUM** → auto-apply
- **HIGH** → blocked, Slack notification sent, board member must run approve-override workflow
- **FAILED** → blocked (review script error)

## Plan Artifact Security

Plans are stored in S3 (`javabin-ci-plan-artifacts-553637109631`) with 24h lifecycle expiry.
The SHA256 hash is computed at plan time and verified before apply, preventing plan tampering.
