# Phase 4 — Onboard Existing Apps

## Agent Brief

You are onboarding Javabin's existing applications onto the golden path platform built in
Phases 0–3. The platform is fully operational — CI workflows, registry, monitoring, and
IAM roles all work. Your job is to migrate each app with minimal disruption.

**Prerequisites:** All prior phases complete and verified. Platform CI handles all infra applies.
Registry is operational. Monitoring is live.

**Repo:** Work happens across multiple repos — `javaBin/registry`, each app repo, and
`javaBin/platform` for any platform-side changes.

**AWS Region:** eu-west-1 (confirmed)
**Domain:** java.no
**GitHub Org:** javaBin

---

## Context: Existing Javabin Services

| Service | Repo | Stack | Current Deploy | Priority |
|---|---|---|---|---|
| cake-redux | javaBin/cake-redux | Java/Kotlin/TS, Maven, Docker, CDK | Elastic Beanstalk | 3rd |
| moresleep | javaBin/moresleep | Kotlin, Maven, Docker, Postgres | Elastic Beanstalk | 2nd |
| cupcake | javaBin/cupcake | Kotlin | Unknown | Later |
| annual-meeting-membership | javaBin/annual-meeting-membership | Kotlin | Unknown | Later |
| submittheforce | javaBin/submittheforce | Unknown | Unknown | 4th (needs dev env) |
| 2025.javazone.no-native | javaBin/2025.javazone.no-native | TypeScript, React Native | Unknown | 5th |
| java.no | javaBin/java.no | TypeScript | Unknown | 5th |

**Priority order:**
1. **Platform test repo** — proof of concept (low-stakes throwaway or simple existing repo)
2. **moresleep** — simpler stack, good second candidate
3. **cake-redux** — higher complexity (CDK + Maven + Docker + EB)
4. **submittheforce, javazone.no** — conference-facing, add dev environment
5. Remaining active repos by recent commit activity
6. Leave archived/legacy repos alone (javazone-2020, androidito, etc.)

---

## Onboarding Protocol (Same for Every Repo)

### Step 1: Add Workflow File

The developer adds one file to their repo:
```yaml
# .github/workflows/deploy.yml
name: Deploy
on:
  push:
    branches: [main, master]
  pull_request:

jobs:
  javabin:
    uses: javaBin/platform/.github/workflows/javabin.yml@main
    secrets: inherit
```

### Step 2: Open Registration PR

Either via CLI (`javabin register`) or manually:
```yaml
# registry/apps/{name}.yaml
name: cake-redux
team: pkom
repo: javaBin/cake-redux
existing: true  # triggers discovery before creating resources
```

The `existing: true` flag tells the provisioning workflow to:
1. Run AWS discovery (`aws resourcegroupstaggingapi get-resources`)
2. Check for existing EB environments, CloudFormation stacks, running EC2
3. List findings in Slack for infra owner review
4. Only create what's genuinely missing
5. NEVER duplicate existing resources

### Step 3: Merge Registration PR

Board member reviews and merges. Platform provisioning runs:
- Creates project IAM roles (scoped, with permission boundary)
- Creates ECR repository with lifecycle policy
- Creates AWS Budget (default 1000 NOK threshold)
- Runs resource discovery, posts to `#javabin-infra-alerts`
- Creates SSM parameter namespace `/javabin/apps/{name}/`
- Does NOT touch existing EB environments or CloudFormation stacks

### Step 4: CI is Live

From the first PR after merge:
- If `.elasticbeanstalk/` exists → EB deploy path runs automatically
- Plan review, cost analysis, CloudTrail attribution all work immediately
- Tags injected on new resources going forward
- Developer continues working exactly as before during transition

### Step 5: ECS Migration (Developer's Pace)

Only when the developer is ready:
1. Add `app.yaml` or ECS task definition to the repo
2. CI detects ECS path, runs both EB and ECS deploy in parallel initially
3. Developer verifies ECS is working
4. Developer removes `.elasticbeanstalk/` folder
5. EB environment decommissioned (manual, developer confirms)
6. Clean up dead CI config (`.travis.yml` in cake-redux)

---

## App-Specific Migration Notes

### Platform Test Repo (FIRST — proof of concept)

Either create `javaBin/platform-test` or pick a simple existing repo with a Dockerfile.
**Do NOT use cake-redux or moresleep as the first test.**

Test checklist:
- [ ] Registration PR → provisioning runs → IAM roles created
- [ ] CI triggers on PR → detect → build → plan → review
- [ ] Plan review posts PR comment with risk + cost
- [ ] Merge to main → tf-apply runs → ECS deploys
- [ ] Slack alerts fire for resource creation
- [ ] Cost tracking shows up in daily/weekly reports
- [ ] Direct push (no PR) → review goes to Slack instead
- [ ] HIGH risk plan → deploy blocked → override workflow → deploy proceeds
- [ ] CloudTrail shows GitHub actor attribution
- [ ] `javabin status` shows correct data
- [ ] `javabin whoami` returns correct identity

### moresleep (2nd)

- Kotlin, Maven, Dockerfile, Postgres via docker-compose
- Currently on Elastic Beanstalk
- Simpler stack — no CDK, no complex frontend

Migration steps:
1. Register via `apps/moresleep.yaml` with `existing: true`
2. Discovery will find the EB environment — document its config
3. Add `deploy.yml` workflow
4. CI picks up `.elasticbeanstalk/` → EB deploy works immediately
5. Add `app.yaml` with compute + database config
6. CI generates ECS task definition + RDS (or keep Postgres external)
7. Test ECS deployment in parallel with EB
8. Switch DNS from EB to ALB once confirmed
9. Remove `.elasticbeanstalk/` directory
10. Decommission EB environment (manual)

### cake-redux (3rd)

- Java/Kotlin/TypeScript, Maven, Dockerfile, CDK folder
- Currently on Elastic Beanstalk
- Has `.github/workflows` already — coordinate with existing CI
- Has `.travis.yml` — remove (dead config)
- CDK folder may have live CloudFormation stacks — CHECK FIRST

Migration steps:
1. Register via `apps/cake-redux.yaml` with `existing: true`
2. Discovery finds EB environment AND checks for live CloudFormation stacks from CDK
3. If CloudFormation stacks exist:
   - Document what resources they manage
   - Import those resources into Terraform state before touching CDK
   - Do NOT delete CDK stacks until Terraform has the resources
4. Add `deploy.yml` workflow (may need to coordinate with existing `.github/workflows`)
5. EB deploy works immediately via detection
6. Remove `.travis.yml`
7. Add `app.yaml` for ECS migration
8. Test, switch, decommission EB

### submittheforce (4th — conference-facing, needs dev environment)

- Current CFP submission system for speakers
- Conference-facing → broken CFP during submission causes real damage
- Needs staging + production environments

```yaml
# app.yaml
name: submittheforce
team: pkom
environments:
  prod:
    compute: { cpu: 512, memory: 1024 }
    domain: submit.javazone.no
  dev:
    compute: { cpu: 256, memory: 512 }
    domain: dev.submit.javazone.no
    auto_deploy: true
    budget_alert_nok: 200
```

GitHub Environments:
- `staging` — no reviewers, auto-deploys on main merge
- `production` — required board reviewer, triggered by semver tag

### javazone.no / 2025.javazone.no-native (5th)

- TypeScript projects
- Conference-facing → needs dev environment (same as submittheforce)
- Uses TypeScript CI image

---

## Dev/Prod Environment Model

**Internal tools** (cake-redux, moresleep, cupcake): Single production environment.
Fast rollback is the safety net — golden path ECS retains previous 2 task def revisions.

**Conference-facing** (submittheforce, javazone.no, native app): Staging required.
```
PR         → plan/review only, no deploy
main merge → auto-deploy to staging
tag v*.*   → requires board approval → deploy to production
```

Staging infra: 256 CPU / 512MB ECS task, separate RDS schema or instance, same Cognito pool
with different app client. ~100–150 NOK/month extra.

---

## Verification After Each App

After onboarding each app, verify:

1. **CI pipeline works end-to-end**: PR → plan → review → merge → apply → deploy
2. **Slack alerts fire**: resource creation shows in `#javabin-infra-alerts`
3. **Cost tracking**: app appears in daily/weekly cost reports with correct project tag
4. **CloudTrail attribution**: changes attributed to correct GitHub actor
5. **Tag compliance**: all resources tagged with project and team
6. **Budget**: per-app budget created and alerting configured
7. **Rollback works**: previous task definition can be activated
8. **Health check passes**: ALB target group shows healthy targets
9. **DNS resolves**: `{app}.java.no` routes to the correct service
10. **Auth works**: if `auth: internal`, Cognito login flow works

---

## Constraints

- **Do NOT use production services as the first test.** Use a throwaway or low-stakes repo.
- **Do NOT delete existing EB infrastructure until ECS is confirmed working.**
- **Do NOT delete CDK stacks until Terraform has imported those resources.**
- **Do NOT force-migrate.** Apps move at the developer's pace.
- **Do NOT remove EB deploy from the workflow until the developer confirms ECS is working.**
- **Leave archived repos alone.** javazone-2020, androidito, etc. are not migrated.
- **"Approved repo" means IAM gate.** If a repo is not in registry, it has no role to assume.
  CI gets auth error. Registry PR is the IAM gate, not just a process gate.

## Deliverables

1. `registry/apps/` — registration YAMLs for each onboarded app
2. `registry/teams/` — initial team definitions (core, nettside, pkom, platform-owners)
3. Per-app migration runbooks documenting EB environments, CDK stacks, DNS changes
4. Verification checklist results for each onboarded app
5. Updated CLAUDE.md with any new patterns discovered during migration

## What Success Looks Like

- Every active repo has the golden path CI with plan review, cost analysis, and attribution
- Heroes add one file and open one PR to get full CI/CD
- EB environments are decommissioned (or on a clear timeline)
- Platform handles all infrastructure — no manual AWS Console changes
- Costs are tracked per-project and per-team
- Security alerts flow to Slack
- Identity is federated through Google Workspace → Cognito
