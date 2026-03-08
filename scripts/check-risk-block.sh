#!/bin/sh
# Block apply if risk is HIGH or FAILED. Notify Slack with override link.
#
# Usage: check-risk-block.sh <risk_level> <ssm_param> <override_url>
#
# Exits 0 if safe to apply, 1 if blocked.

set -e

RISK="$1"
SSM_PARAM="$2"
OVERRIDE_URL="$3"
SCRIPT_DIR=$(dirname "$0")

echo "LLM review risk: ${RISK}"

if [ "$RISK" != "HIGH" ] && [ "$RISK" != "FAILED" ] && [ -n "$RISK" ]; then
  exit 0
fi

echo "Auto-apply blocked (risk=${RISK})."

export SSM_WEBHOOK_PARAM="$SSM_PARAM"
sh "$SCRIPT_DIR/notify-slack.sh" \
  "Deploy Blocked — ${RISK} Risk" \
  "*Repo:* ${GITHUB_REPOSITORY}\n*SHA:* \`$(echo "$GITHUB_SHA" | cut -c1-8)\`\n*Actor:* ${GITHUB_ACTOR}" \
  "$OVERRIDE_URL" \
  "Approve Override" || true

exit 1
