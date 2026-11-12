# Platform CI Workflow

Defined in `.github/workflows/platform-ci.yml`. Runs on pushes to `main` affecting `terraform/**`, `scripts/**`, or `.github/workflows/**`, and on all pull requests.

## Pipeline

```
push to main ──► plan (includes LLM review) ──► apply
PR             ──► plan (includes LLM review) ──► PR comments (no apply)
Monday 06:00   ──► drift detection ──► Slack if drift found
Monday 08:00   ──► securityhub-summary Lambda ──► #javabin-infra-alerts
```

## Jobs

### terraform-format and validate-modules
- Run on non-scheduled workflow events, with no AWS credentials.
- Check formatting across all Terraform roots and modules with `terraform fmt -check -diff -recursive terraform`.
- Discover each directory under `terraform/modules/` and run `terraform init -backend=false` followed by `terraform validate`. Provider downloads are reused within the job.
- Both checks must pass before the platform apply job can run.
- Validation checks configuration correctness, not lifecycle-policy retention semantics. App module changes are deployed by the consuming app's Terraform plan/apply workflow after it fetches the updated module.

### plan (includes LLM review)
- OIDC → `javabin-ci-infra-plan` role (read-only)
- `terraform init`, `validate`, `plan` in `terraform/platform`, including its referenced child modules. The parent `terraform/` directory is not a Terraform root configuration; `org/` and `state/` remain separate, human-applied roots.
- Uploads `tfplan` + `plan-output.txt` to S3 with SHA256 hash
- Posts plan output as PR comment on pull requests
- Runs `scripts/review-plan.py` inline — calls Bedrock (Claude Haiku 5.5) for risk analysis
- The model gets the whole plan with refresh/read lines stripped; there is no practical input cap (Haiku 5.5 has a 1M-token context): the default ceiling is 2M chars, 2.4x the largest real plan. The prompt says the plan is complete, gives its change count and `Plan:` line, and explains Terraform's own elision markers (`# (N unchanged attributes hidden)`, `(sensitive value)`, `(known after apply)`, partial `jsonencode` diffs) so the model does not report them as truncation. `REVIEW_MAX_PLAN_CHARS` overrides the ceiling. A plan above it gets a list of every resource change plus as much diff as fits, cut at a resource boundary, and the prompt says exactly which diffs are missing. A response with `stopReason: max_tokens` is discarded (FAILED / retried) rather than trusted.
- The PR comment shows at most 60k chars of the plan (GitHub's comment limit) and says so when it cuts; this does not affect the review.
- Structured output via Bedrock Converse tool use: `{risk: LOW|MEDIUM|HIGH, summary: ..., findings: [...]}`
- Posts review as PR comment
- HIGH risk (or a FAILED review) on main → Slack alert to #platform-override-alerts with plan key, run ID and override button. `slack_post.py` checks Slack's reply; rejected blocks fall back to plain text, and an undelivered alert shows as a CI error annotation
- Outputs: `has_changes`, `plan_key`, `plan_sha256`, `risk_level`

### apply
- Only on `main` (`push`, `workflow_dispatch`, or the registry's `teams-changed` `repository_dispatch`) with changes
- Checks risk level: blocks on HIGH/FAILED/UNKNOWN
- Downloads plan from S3, verifies SHA256 integrity
- Restores `.terraform.lock.hcl` from the plan file (Terraform stores it there; it is gitignored in the repo), so `terraform init` installs the provider versions the plan was made with
- `terraform apply -auto-approve tfplan`
- Then queues (async, non-blocking) an app-auth-sync reconcile (`scripts/reconcile-app-auth.sh`) and a team-provisioner `sync_team_access` run (`scripts/sync-team-access.sh`), which grants a newly registered team its permission set and account assignment once this apply has created its `javabin-team-{team}-guardrails` policy
- Uses `environment: production` (GitHub environment protection)
- OIDC → `javabin-ci-infra`. The role's trust also pins `job_workflow_ref` to `platform-ci.yml@refs/heads/main`, because the environment-based `sub` claim carries no branch. A workflow on another branch that declares `environment: production` cannot assume it.

### drift
- Weekly schedule (Monday 06:00 UTC)
- OIDC → `javabin-ci-infra-plan` (read-only; drift never applies)
- Runs `terraform plan -detailed-exitcode`
- If drift detected, posts to Slack with plan output

## Risk Gate

- **LOW/MEDIUM** → auto-apply
- **HIGH** → blocked, Slack notification sent, board member must run approve-override workflow
- **FAILED** → blocked (the review could not run; a service failure, not a risk finding). See [Requesting an override](apply-gate.md#requesting-an-override)

## Plan Artifact Security

Plans are stored in S3 (`javabin-ci-plan-artifacts-553637109631`) with 24h lifecycle expiry.
The SHA256 hash is computed at plan time and verified before apply, preventing plan tampering.
