#!/bin/sh
# Post a Block Kit message to Slack via incoming webhook.
#
# Usage: notify-slack.sh <title> <message> [url] [url_label]
#
# The webhook URL is resolved in order:
#   1. SLACK_WEBHOOK_URL env var (if set)
#   2. SSM_WEBHOOK_PARAM env var → fetched from AWS SSM
#
# If neither is set, the notification is silently skipped. slack_post.py builds
# the payload (escaped, size-limited), checks Slack's reply and falls back to
# plain text if the blocks are rejected.

set -e

TITLE="$1"
MESSAGE="$2"
URL="${3:-}"
URL_LABEL="${4:-View}"

# Resolve webhook URL
if [ -z "$SLACK_WEBHOOK_URL" ] && [ -n "$SSM_WEBHOOK_PARAM" ]; then
  SLACK_WEBHOOK_URL=$(aws ssm get-parameter \
    --name "$SSM_WEBHOOK_PARAM" \
    --with-decryption --query Parameter.Value --output text 2>/dev/null || echo "")
fi

if [ -z "$SLACK_WEBHOOK_URL" ]; then
  echo "No webhook URL available — skipping Slack notification."
  exit 0
fi

SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)
export SLACK_WEBHOOK_URL
python3 "$SCRIPT_DIR/slack_post.py" message "$TITLE" "$MESSAGE" "$URL" "$URL_LABEL" ||
  echo "::error::Slack notification '${TITLE}' was not delivered (see above)."
