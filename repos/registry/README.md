# Javabin Registry

Central registry for app and team registration. Merging a PR here triggers infrastructure provisioning in the platform.

## Registering an App

1. Create `apps/your-service.yaml`:
```yaml
name: your-service
team: your-team
repo: javaBin/your-service
```

2. Open a PR. CI validates the YAML and checks that the team exists.
3. A platform owner reviews and merges.
4. Merge triggers IAM role creation, ECR repo, and budget setup.

## Registering a Team

1. Create `teams/your-team.yaml`:
```yaml
name: your-team
description: What your team works on
google_group: team-yourteam@java.no
members:
  - username1
  - username2
repos:
  - repo-name
aws_budget_nok: 3000
```

2. Open a PR and get it reviewed by a platform owner.

## App Registration Schema

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Service name, matches repo name |
| `team` | Yes | Team name, must exist in `teams/` |
| `repo` | Yes | Full repo path, e.g. `javaBin/cake-redux` |
| `existing` | No | Set `true` for pre-existing apps migrating to platform |

## Team Definition Schema

| Field | Required | Description |
|-------|----------|-------------|
| `name` | Yes | Team identifier |
| `description` | Yes | What the team does |
| `google_group` | No | Google Workspace group email |
| `members` | Yes | List of member usernames |
| `repos` | Yes | List of repo names this team owns |
| `aws_budget_nok` | No | Team-level monthly budget in NOK |

## How It Works

```
Developer opens PR ──> CI validates YAML + team refs
                   ──> Slack notification to #javabin-infra-alerts
Platform owner merges ──> repository_dispatch to javaBin/platform
                      ──> Platform creates IAM roles, ECR, Budget
```

The registry is the IAM gate: unregistered repos have no IAM role to assume, so their CI pipelines cannot deploy.

## Access

- Private repo, visible to javaBin org members
- Any org member can open a registration PR
- Merges require review from `@javaBin/platform-owners`
