#!/bin/sh
# Invoke the apply-gate Lambda to get temporary credentials for terraform apply.
#
# Usage: invoke-apply-gate.sh <plan_key> <repo_name>
#
# On success: exports AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN
# On failure: exits 1 with reason
#
# Requires: aws CLI, jq, LAMBDA_NAME env var

set -e

PLAN_KEY="$1"
REPO_NAME="$2"
LAMBDA_NAME="${LAMBDA_NAME:-javabin-apply-gate}"

if [ -z "$PLAN_KEY" ] || [ -z "$REPO_NAME" ]; then
  echo "Usage: invoke-apply-gate.sh <plan_key> <repo_name>"
  exit 1
fi

echo "Requesting apply credentials from gate Lambda..."

# Build session name from GitHub context for alert attribution
GATE_SESSION="${GITHUB_ACTOR:-unknown}-$(echo "${GITHUB_SHA:-00000000}" | cut -c1-8)-${GITHUB_RUN_ID:-0}"

PAYLOAD=$(jq -n \
  --arg action "check" \
  --arg plan_key "$PLAN_KEY" \
  --arg repo_name "$REPO_NAME" \
  --arg session_name "$GATE_SESSION" \
  '{action: $action, plan_key: $plan_key, repo_name: $repo_name, session_name: $session_name}')

RESPONSE=$(aws lambda invoke \
  --function-name "$LAMBDA_NAME" \
  --payload "$PAYLOAD" \
  --cli-binary-format raw-in-base64-out \
  /dev/stdout 2>/dev/null)

BODY=$(echo "$RESPONSE" | jq -r '.body // empty' 2>/dev/null)
if [ -z "$BODY" ]; then
  BODY="$RESPONSE"
fi

APPROVED=$(echo "$BODY" | jq -r '.approved // false')
RISK=$(echo "$BODY" | jq -r '.risk_level // "UNKNOWN"')
REASON=$(echo "$BODY" | jq -r '.reason // empty')

echo "Risk level: $RISK"

if [ "$APPROVED" != "true" ]; then
  echo "Apply blocked: ${REASON:-risk gate denied}"

  # Notify Slack about the block
  if [ -n "$SSM_WEBHOOK_PARAM" ]; then
    SCRIPT_DIR=$(dirname "$0")
    RUN_URL="https://github.com/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
    sh "$SCRIPT_DIR/notify-slack.sh" \
      "Terraform Apply Blocked" \
      "*Repo:* ${GITHUB_REPOSITORY}\n*Risk:* ${RISK}\n*Reason:* ${REASON:-override required}\n<${RUN_URL}|View Run>" || true
  fi

  exit 1
fi

# Export credentials for terraform
ACCESS_KEY=$(echo "$BODY" | jq -r '.credentials.AccessKeyId')
SECRET_KEY=$(echo "$BODY" | jq -r '.credentials.SecretAccessKey')
SESSION_TOKEN=$(echo "$BODY" | jq -r '.credentials.SessionToken')

echo "AWS_ACCESS_KEY_ID=${ACCESS_KEY}" >> "$GITHUB_ENV"
echo "AWS_SECRET_ACCESS_KEY=${SECRET_KEY}" >> "$GITHUB_ENV"
echo "AWS_SESSION_TOKEN=${SESSION_TOKEN}" >> "$GITHUB_ENV"

# Mask credentials in logs
echo "::add-mask::${ACCESS_KEY}"
echo "::add-mask::${SECRET_KEY}"
echo "::add-mask::${SESSION_TOKEN}"

OVERRIDE_BY=$(echo "$BODY" | jq -r '.override.approved_by // empty')
if [ -n "$OVERRIDE_BY" ]; then
  echo "Apply approved via override by ${OVERRIDE_BY}"
else
  echo "Apply approved (risk: ${RISK})"
fi
