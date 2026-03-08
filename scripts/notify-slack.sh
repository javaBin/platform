#!/bin/sh
# Post a Block Kit message to Slack via incoming webhook.
#
# Usage: notify-slack.sh <title> <message> [url] [url_label]
#
# The webhook URL is resolved in order:
#   1. SLACK_WEBHOOK_URL env var (if set)
#   2. SSM_WEBHOOK_PARAM env var → fetched from AWS SSM
#
# If neither is set, the notification is silently skipped.

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

# Build blocks JSON
URL_BLOCK=""
if [ -n "$URL" ]; then
  URL_BLOCK=",{\"type\":\"section\",\"text\":{\"type\":\"mrkdwn\",\"text\":\"<${URL}|${URL_LABEL}>\"}}"
fi

PAYLOAD=$(cat <<EOF
{
  "blocks": [
    {"type":"header","text":{"type":"plain_text","text":"${TITLE}","emoji":true}},
    {"type":"section","text":{"type":"mrkdwn","text":"${MESSAGE}"}}
    ${URL_BLOCK}
  ],
  "text": "${TITLE}"
}
EOF
)

curl -s -X POST -H "Content-Type: application/json" -d "$PAYLOAD" "$SLACK_WEBHOOK_URL" > /dev/null
echo "Slack notification sent: ${TITLE}"
