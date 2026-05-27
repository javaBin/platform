#!/bin/sh
# Invoke the team-provisioner Lambda with team YAML files.
# The Lambda reconciles Google Groups, GitHub teams, and AWS Budgets.
#
# Usage: provision-teams.sh <team1.yaml> [team2.yaml ...]
#
# Requires: aws CLI, yq (pre-installed on GitHub runners)

set -e

if [ $# -eq 0 ]; then
  echo "Usage: provision-teams.sh <team1.yaml> [team2.yaml ...]" >&2
  exit 1
fi

TEAMS="[]"
for file in "$@"; do
  [ -f "$file" ] || continue
  TEAM=$(yq -o json "$file")
  NAME=$(echo "$TEAM" | yq -r '.name')
  MEMBERS=$(echo "$TEAM" | yq '.members | length')
  echo "  ${NAME}: ${MEMBERS} member(s)"
  TEAMS=$(echo "$TEAMS" | yq -o json ". + [${TEAM}]")
done

COUNT=$(echo "$TEAMS" | yq 'length')
if [ "$COUNT" -eq 0 ]; then
  echo "No valid team definitions — skipping."
  exit 0
fi

echo "Invoking javabin-team-provisioner with ${COUNT} team(s)..."

# Build CI context from GitHub Actions environment (empty object if not in CI)
CI_CONTEXT="{}"
if [ -n "$GITHUB_ACTOR" ]; then
  CI_CONTEXT=$(printf '{"actor":"%s","repo":"%s","sha":"%s","run_id":"%s"}' \
    "${GITHUB_ACTOR}" "${GITHUB_REPOSITORY}" "${GITHUB_SHA}" "${GITHUB_RUN_ID}")
fi

PAYLOAD=$(echo "$TEAMS" | yq -o json '{"teams": .}')
PAYLOAD=$(echo "$PAYLOAD" | yq -o json ". + {\"ci_context\": ${CI_CONTEXT}}")

aws lambda invoke \
  --function-name javabin-team-provisioner \
  --payload "$PAYLOAD" \
  --cli-binary-format raw-in-base64-out \
  --cli-read-timeout 0 \
  /tmp/lambda-response.json

cat /tmp/lambda-response.json
