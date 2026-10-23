#!/bin/sh
# Ask the plan-review Lambda to review an uploaded app plan.
#
# Usage: invoke-plan-review.sh <plan_key>
#
# Runs with the javabin-ci-app-broker OIDC role (team roles have no Bedrock
# access). The Lambda verifies this job's GitHub OIDC token, reviews the
# plan text next to <plan_key>, and writes risk.json itself.
#
# Env: NOTIFY=true to post HIGH/FAILED to the override Slack channel.
# Output: review-output.txt (markdown for the PR comment),
#         risk_level=<LOW|MEDIUM|HIGH|FAILED> in GITHUB_OUTPUT.

set -e

PLAN_KEY="${1:?Usage: invoke-plan-review.sh <plan_key>}"
ID_TOKEN=$(sh "$(dirname "$0")/github-oidc-token.sh" "${PROJECT:-javabin}-plan-review")

# A failed invoke is a FAILED review, not a failed job: the PR still gets a
# comment and the gate still blocks.
echo '{}' > /tmp/plan-review.json
aws lambda invoke \
  --function-name "${PROJECT:-javabin}-plan-review" \
  --cli-read-timeout 310 \
  --payload "$(jq -nc --arg plan_key "$PLAN_KEY" --arg id_token "$ID_TOKEN" --argjson notify "${NOTIFY:-false}" \
    '{plan_key: $plan_key, id_token: $id_token, notify: $notify}')" \
  --cli-binary-format raw-in-base64-out \
  /tmp/plan-review.json >/dev/null || echo '{"error": "could not invoke the plan-review Lambda"}' > /tmp/plan-review.json

ERROR=$(jq -r '.error // .errorMessage // (if .risk then empty else "empty response" end)' /tmp/plan-review.json)
if [ -n "$ERROR" ]; then
  echo "::error::Plan review failed: $ERROR"
  RISK=FAILED
  printf '**Risk: ⚪ FAILED**\n\nPlan review could not run: %s\n' "$ERROR" > review-output.txt
else
  RISK=$(jq -r '.risk' /tmp/plan-review.json)
  jq -r '.markdown' /tmp/plan-review.json > review-output.txt
fi

cat review-output.txt
echo "risk_level=${RISK}" >> "${GITHUB_OUTPUT:-/dev/null}"
echo "Plan review risk: ${RISK}"
