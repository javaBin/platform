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

PAYLOAD=$(yq -o json '{"action": "sync_groups", "groups": .groups}' "$GROUPS_FILE")

aws lambda invoke \
  --function-name javabin-team-provisioner \
  --payload "$PAYLOAD" \
  --cli-binary-format raw-in-base64-out \
  /tmp/lambda-response.json

cat /tmp/lambda-response.json
