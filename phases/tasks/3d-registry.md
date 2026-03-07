# Task 3d — Registry Repo

**Status:** Not started
**Phase:** 3 (Golden Path)
**Depends on:** 2f (team-provisioner Lambda), 2c (IAM roles created from registry)
**Blocks:** Phase 4 (apps register via registry)

## Objective

Create the `javaBin/registry` repo structure, YAML schemas, validation workflows,
and provisioning triggers.

## Context

Read `phases/phase-3-golden-path.md` Task 3 for full spec.
Read `PLAN.md` sections on Registry and Teams.

## Deliverables

### Repo Structure
```
registry/
  apps/
    .schema.yaml        # JSON Schema for app registration
  teams/
    .schema.yaml        # JSON Schema for team definition
  .github/
    workflows/
      validate.yml      # PR: validate YAML schema + team references
      notify.yml        # PR: post to Slack
      provision.yml     # Merge: trigger platform provisioning
    CODEOWNERS
  README.md
```

### App Registration Schema
```yaml
name: string        # required, matches repo name
team: string        # required, must exist in teams/
repo: string        # required, format: javaBin/{name}
existing: boolean   # optional, triggers discovery before provisioning
```

### Team Definition Schema
```yaml
name: string
description: string
google_group: string    # team-{name}@java.no
members: list[string]   # usernames (before @java.no)
repos: list[string]     # repo names this team owns
aws_budget_nok: number  # team-level budget
```

### Workflows

**validate.yml** (on PR):
- Validate YAML against schema
- Check team references exist in teams/
- Check repo exists in javaBin org (via GitHub API)
- Post validation result as PR check

**notify.yml** (on PR opened/updated):
- Post to `#javabin-infra-alerts`: new registration request + PR link
- No Slack interactive buttons — approve/deny in GitHub

**provision.yml** (on merge to main):
- Determine which files changed
- Send `repository_dispatch` to `javaBin/platform` with payload
- Platform receives dispatch → creates IAM roles, ECR, Budget, etc.

### Access Model
- Private repo, visible to org members
- Any org member can open registration PR
- Main branch: required reviewer from `@javaBin/platform-owners`
- Merge = IAM gate — no registry entry = no IAM role = CI auth fails

## Rules
- Registry is the IAM gate — unregistered repos have no role to assume
- Team names in apps/ must reference existing teams/
- YAML validation runs in CI before merge
- repository_dispatch is the only trigger for platform provisioning
- Registry is PRIVATE (contains usernames and team membership)
