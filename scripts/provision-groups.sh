#!/bin/sh
# Invoke the team-provisioner Lambda with group definitions from groups.yaml.
#
# Usage: provision-groups.sh <groups.yaml>
#
# The Lambda syncs groups to Cognito and Identity Center based on the
# cognito/identity_center/permission_set fields in each group entry.
#
# Requires: aws CLI, yq

set -e

GROUPS_FILE="${1:-groups.yaml}"

if [ ! -f "$GROUPS_FILE" ]; then
  echo "No groups.yaml found — skipping."
  exit 0
fi

COUNT=$(yq '.groups | length' "$GROUPS_FILE")
echo "Provisioning ${COUNT} group(s) from ${GROUPS_FILE}..."

# Build CI context from GitHub Actions environment
CI_CONTEXT="{}"
if [ -n "$GITHUB_ACTOR" ]; then
  CI_CONTEXT=$(printf '{"actor":"%s","repo":"%s","sha":"%s","run_id":"%s"}' \
    "${GITHUB_ACTOR}" "${GITHUB_REPOSITORY}" "${GITHUB_SHA}" "${GITHUB_RUN_ID}")
fi

PAYLOAD=$(yq -o json '{"action": "sync_groups", "groups": .groups}' "$GROUPS_FILE")
PAYLOAD=$(echo "$PAYLOAD" | yq -o json ". + {\"ci_context\": ${CI_CONTEXT}}")

aws lambda invoke \
  --function-name javabin-team-provisioner \
  --payload "$PAYLOAD" \
  --cli-binary-format raw-in-base64-out \
  /tmp/lambda-response.json

cat /tmp/lambda-response.json
