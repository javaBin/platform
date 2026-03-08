#!/bin/sh
# Notify Slack about a HIGH risk plan review with override link.
#
# Usage: notify-high-risk.sh <ssm_param> <override_url>
#
# Reads review-result.json from current directory.

set -e

SSM_PARAM="$1"
OVERRIDE_URL="$2"
SCRIPT_DIR=$(dirname "$0")

SUMMARY=$(jq -r '.summary // "Review failed"' review-result.json 2>/dev/null || echo "Review failed")

export SSM_WEBHOOK_PARAM="$SSM_PARAM"
sh "$SCRIPT_DIR/notify-slack.sh" \
  "Deploy Blocked — HIGH Risk Plan" \
  "*Summary:* ${SUMMARY}" \
  "$OVERRIDE_URL" \
  "Approve Override"
