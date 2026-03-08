#!/bin/sh
# Check risk level and block apply if HIGH without an override token.
#
# Usage: check-risk-gate.sh <risk_level> <repo> <sha> <ssm_webhook_param>
#
# Exits 0 if apply should proceed, 1 if blocked.
# On block: consumes override token from SSM or posts Slack alert and fails.

set -e

RISK="$1"
REPO="$2"
SHA="$3"
SSM_PARAM="$4"
SCRIPT_DIR=$(dirname "$0")

echo "LLM review risk: ${RISK}"

if [ "$RISK" = "LOW" ] || [ "$RISK" = "MEDIUM" ]; then
  exit 0
fi

echo "Checking for override token..."
OVERRIDE_KEY="/javabin/platform-overrides/${REPO}/${SHA}"

set +e
aws ssm get-parameter --name "$OVERRIDE_KEY" --query Parameter.Value --output text > /dev/null 2>&1
OVERRIDE_EXISTS=$?
set -e

if [ "$OVERRIDE_EXISTS" = "0" ]; then
  echo "Override token found — applying despite ${RISK} risk."
  aws ssm delete-parameter --name "$OVERRIDE_KEY" 2>/dev/null || true
  exit 0
fi

echo "Auto-apply blocked (risk=${RISK}, no override token)."

RUN_URL="https://github.com/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
OVERRIDE_URL="https://github.com/javaBin/platform/actions/workflows/approve-override.yml"

export SSM_WEBHOOK_PARAM="$SSM_PARAM"
sh "$SCRIPT_DIR/notify-slack.sh" \
  "Terraform Apply Blocked" \
  "*Repo:* ${GITHUB_REPOSITORY}\n*Reason:* ${RISK} risk — override required\n<${RUN_URL}|View Workflow> | <${OVERRIDE_URL}|Approve Override>" || true

exit 1
