#!/bin/sh
# Write a risk gate override token to SSM.
#
# Usage: write-override-token.sh <repo> <sha> <actor> <reason>

set -e

REPO="$1"
SHA="$2"
ACTOR="$3"
REASON="$4"

PARAM_NAME="/javabin/platform-overrides/${REPO}/${SHA}"
VALUE=$(jq -n \
  --arg by "$ACTOR" \
  --arg reason "$REASON" \
  --arg at "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  --arg run "${GITHUB_RUN_ID:-}" \
  '{approved_by: $by, reason: $reason, approved_at: $at, run_id: $run}')

aws ssm put-parameter \
  --name "$PARAM_NAME" \
  --type String \
  --value "$VALUE" \
  --overwrite

echo "Override token written: ${PARAM_NAME}"
