#!/bin/sh
# Ask the plan-review Lambda to review an uploaded app plan.
#
# Usage: invoke-plan-review.sh <plan_key>
#
# Runs with the javabin-ci-app-broker OIDC role (team roles have no Bedrock
# access). The Lambda verifies this job's GitHub OIDC token, reviews the
# plan text next to <plan_key>, and writes risk.json itself.
#
# A FAILED review means the review service did not produce a verdict (invoke
# error, Lambda timeout, Bedrock outage); it is not a risk finding. Those are
# retried up to REVIEW_INVOKE_ATTEMPTS times (default 3), REVIEW_RETRY_DELAY
# seconds apart (doubling). Refusals (bad OIDC token, someone else's plan)
# are not retried. The gate still blocks a plan whose last verdict is FAILED.
#
# Env: NOTIFY=true to post HIGH/FAILED to the override Slack channel (FAILED
#      only once retries are used up).
# Output: review-output.txt (markdown for the PR comment),
#         risk_level=<LOW|MEDIUM|HIGH|FAILED> in GITHUB_OUTPUT.

set -e

PLAN_KEY="${1:?Usage: invoke-plan-review.sh <plan_key>}"
ATTEMPTS="${REVIEW_INVOKE_ATTEMPTS:-3}"
DELAY="${REVIEW_RETRY_DELAY:-20}"
ID_TOKEN=$(sh "$(dirname "$0")/github-oidc-token.sh" "${PROJECT:-javabin}-plan-review")

attempt=1
while :; do
  # The caller retries a FAILED result itself, so the Lambda alerts on it only
  # on the last attempt (HIGH is final and always alerts).
  RETRYING=false
  [ "$attempt" -lt "$ATTEMPTS" ] && RETRYING=true

  # A failed invoke is a FAILED review, not a failed job: the PR still gets a
  # comment and the gate still blocks.
  echo '{}' > /tmp/plan-review.json
  aws lambda invoke \
    --function-name "${PROJECT:-javabin}-plan-review" \
    --cli-read-timeout 310 \
    --payload "$(jq -nc --arg plan_key "$PLAN_KEY" --arg id_token "$ID_TOKEN" \
      --argjson notify "${NOTIFY:-false}" --argjson retrying "$RETRYING" \
      '{plan_key: $plan_key, id_token: $id_token, notify: $notify, retrying: $retrying}')" \
    --cli-binary-format raw-in-base64-out \
    /tmp/plan-review.json >/dev/null || echo '{"error": "could not invoke the plan-review Lambda"}' > /tmp/plan-review.json

  ERROR=$(jq -r '.error // .errorMessage // (if .risk then empty else "empty response" end)' /tmp/plan-review.json)
  RISK=$(jq -r '.risk // "FAILED"' /tmp/plan-review.json)
  [ -n "$ERROR" ] && RISK=FAILED

  case "$ERROR" in
    "Invalid GitHub OIDC token"*|*"does not belong to"*) break ;;  # refused, retrying will not help
  esac
  [ "$RISK" != "FAILED" ] && break
  [ "$attempt" -ge "$ATTEMPTS" ] && break

  echo "::warning::Plan review service failed (attempt ${attempt}/${ATTEMPTS}): ${ERROR:-no verdict}. Retrying in ${DELAY}s."
  sleep "$DELAY"
  DELAY=$((DELAY * 2))
  attempt=$((attempt + 1))
done

if [ -n "$ERROR" ]; then
  printf '**Risk: ⚪ FAILED**\n\nThe plan-review service could not review this plan: %s\n' "$ERROR" > review-output.txt
else
  jq -r '.markdown' /tmp/plan-review.json > review-output.txt
fi

case "$RISK" in
  FAILED)
    echo "::error title=Plan review service failed::No risk verdict after ${attempt} attempt(s): ${ERROR:-the review did not complete}. This is a service failure, not a risk finding. Terraform Apply will block: re-run all jobs later, or request an override (https://github.com/javaBin/platform/blob/main/docs/apply-gate.md#requesting-an-override)."
    ;;
  HIGH)
    echo "::error title=Plan review found HIGH risk::The reviewer rated this plan HIGH risk. Terraform Apply will block until the platform team approves an override (https://github.com/javaBin/platform/blob/main/docs/apply-gate.md#requesting-an-override)."
    ;;
esac

cat review-output.txt
echo "risk_level=${RISK}" >> "${GITHUB_OUTPUT:-/dev/null}"
echo "Plan review risk: ${RISK}"
