#!/bin/sh
# Ask the apply-gate Lambda to sign a risk override for a plan.
#
# Usage: invoke-override-sign.sh
# approved_by is still sent for the previously deployed gate, which reads
# it; the current gate ignores it and uses the token's actor.
#
# Env: PLAN_KEY, REASON; runs in approve-override.yml (the gate checks the
#      OIDC token comes from that workflow and the override-approval
#      environment, and records the token's actor as approver).

set -e

DIR=$(dirname "$0")
ID_TOKEN=$(sh "$DIR/github-oidc-token.sh" "${PROJECT:-javabin}-apply-gate")

aws lambda invoke \
  --function-name "${PROJECT:-javabin}-apply-gate" \
  --payload "$(jq -nc --arg plan_key "$PLAN_KEY" --arg reason "$REASON" --arg id_token "$ID_TOKEN" \
    --arg approved_by "${GITHUB_ACTOR:-}" \
    '{action: "sign", plan_key: $plan_key, reason: $reason, id_token: $id_token, approved_by: $approved_by}')" \
  --cli-binary-format raw-in-base64-out \
  /tmp/override-response.json >/dev/null

BODY=$(jq -r '.body' /tmp/override-response.json)
if [ "$(echo "$BODY" | jq -r '.signed // false')" != "true" ]; then
  echo "ERROR: override not signed: $(echo "$BODY" | jq -r '.error // "unknown error"')"
  exit 1
fi
echo "Override signed for $PLAN_KEY"
