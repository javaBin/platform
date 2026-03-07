# Task 3e — javabin CLI

**Status:** Not started
**Phase:** 3 (Golden Path)
**Depends on:** 2 (platform infra for status queries), 3d (registry for registration)
**Blocks:** Nothing critical — nice-to-have for developer experience

## Objective

Build the `javabin` CLI tool — registration, status, and identity. Not a deploy tool.

## Context

Read `phases/phase-3-golden-path.md` Task 6 for full spec.
Read `PLAN.md` section on javabin CLI.

## Deliverables

### `javaBin/javabin-cli` repo (local for now)

**Language:** Go. Single static binary.

### Commands

**`javabin register`** — Interactive wizard:
- Prompt: repo name (validate exists in javaBin org)
- Prompt: team (show available from registry)
- Prompt: dev environment needed?
- Prompt: budget threshold (default 1000 NOK)
- Prompt: auth requirement (internal/external/both/none)
- Creates `apps/{name}.yaml`, opens PR against javaBin/registry
- Prints PR URL

**`javabin status`** — Project status:
- AWS spend this month vs budget (Cost Explorer, filtered by project tag)
- Running ECS services + task counts
- Last 5 deployments (commit SHA, actor, timestamp)
- Untagged resources (compliance)
- Month-over-month cost trend

**`javabin whoami`** — Identity info:
- Cognito identity (name, email, groups)
- Current AWS role
- Project scope (from principal tags)
- Token expiry

### Authentication
Lazy device flow against internal Cognito User Pool:
- Check cached token in `~/.javabin/token`
- If expired/missing: trigger device flow inline
- Cognito + Google OAuth (hd=java.no)
- No AWS credentials stored by CLI

### Distribution
- `brew install javaBin/tap/javabin`
- `curl -fsSL https://cli.java.no/install.sh | sh`
- `go install github.com/javaBin/javabin-cli@latest`
- Release: GoReleaser on semver tags → GitHub Releases + homebrew-tap

## Rules
- NO plan, apply, generate, validate, or deploy commands — those are CI-only
- CLI is for registration, status, and identity only
- If a command isn't easier than GitHub/console, don't add it
- Go for single binary, easy cross-compilation
- `javabin status` filters by team automatically (from Cognito token)
