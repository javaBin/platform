#!/bin/sh
# Resolve which team a repo belongs to via GitHub API.
#
# Usage: resolve-team.sh
#
# Env: GITHUB_REPOSITORY (org/repo), GH_TOKEN or GITHUB_TOKEN
# Output: team={slug} written to GITHUB_OUTPUT
#
# Queries /repos/{owner}/{repo}/teams and picks the first non-platform team.
# Fails if the repo doesn't belong to any team (no IAM role to assume).

set -e

REPO="${GITHUB_REPOSITORY:?GITHUB_REPOSITORY required}"

TEAM=$(gh api "/repos/${REPO}/teams" \
  --jq '[.[] | select(.slug != "platform")] | .[0].slug // empty' 2>/dev/null || true)

if [ -z "$TEAM" ]; then
  echo "ERROR: repo ${REPO} does not belong to any GitHub team."
  echo "Add the repo to your team at https://github.com/orgs/javaBin/teams"
  exit 1
fi

echo "Resolved team: ${TEAM}"
echo "team=${TEAM}" >> "${GITHUB_OUTPUT:-/dev/null}"
