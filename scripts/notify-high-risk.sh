#!/bin/sh
# Notify Slack about a HIGH risk plan review with full findings and override link.
#
# Usage: notify-high-risk.sh <ssm_param> <override_url>
#
# Reads review-result.json from current directory.
# Uses GitHub Actions env vars for repo context (GITHUB_REPOSITORY, GITHUB_SHA, GITHUB_ACTOR).

set -e

SSM_PARAM="$1"
OVERRIDE_URL="$2"

# Resolve webhook URL from SSM
SLACK_WEBHOOK_URL=$(aws ssm get-parameter \
  --name "$SSM_PARAM" \
  --with-decryption --query Parameter.Value --output text 2>/dev/null || echo "")

if [ -z "$SLACK_WEBHOOK_URL" ]; then
  echo "No webhook URL available — skipping Slack notification."
  exit 0
fi

# Extract review data
SUMMARY=$(jq -r '.summary // "Review unavailable"' review-result.json 2>/dev/null || echo "Review unavailable")
RISK=$(jq -r '.risk // "HIGH"' review-result.json 2>/dev/null || echo "HIGH")

# Build findings blocks as JSON array entries
FINDINGS_TEXT=""
if [ -f review-result.json ]; then
  FINDINGS_TEXT=$(python3 -c "
import json, sys
try:
    data = json.load(open('review-result.json'))
    cat_emoji = {'security': ':lock:', 'cost': ':moneybag:', 'destruction': ':boom:', 'routine': ':white_check_mark:'}
    lines = []
    for f in data.get('findings', []):
        emoji = cat_emoji.get(f.get('category', ''), ':black_small_square:')
        lines.append(f\"{emoji} *[{f.get('category', 'other')}]* {f.get('detail', '')}\")
    print('\n'.join(lines))
except Exception:
    pass
" 2>/dev/null || echo "")
fi

# Repo context from GitHub Actions
REPO="${GITHUB_REPOSITORY:-unknown}"
SHA=$(echo "${GITHUB_SHA:-unknown}" | cut -c1-8)
ACTOR="${GITHUB_ACTOR:-unknown}"
RUN_ID="${GITHUB_RUN_ID:-}"

# Build source line
if [ -n "$RUN_ID" ] && [ "$REPO" != "unknown" ]; then
  SOURCE_LINE=":robot_face: <https://github.com/${REPO}|${REPO}> / <https://github.com/${REPO}/commit/${GITHUB_SHA}|${SHA}> / <https://github.com/${REPO}/actions/runs/${RUN_ID}|run> (triggered by ${ACTOR})"
else
  SOURCE_LINE=":bust_in_silhouette: ${REPO} / \`${SHA}\` / ${ACTOR}"
fi

# Build the Block Kit payload
# Note: findings section only included if there are findings
FINDINGS_BLOCK=""
if [ -n "$FINDINGS_TEXT" ]; then
  # Escape for JSON
  ESCAPED_FINDINGS=$(echo "$FINDINGS_TEXT" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read())[1:-1])")
  FINDINGS_BLOCK=",{\"type\":\"section\",\"text\":{\"type\":\"mrkdwn\",\"text\":\"*Findings*\n${ESCAPED_FINDINGS}\"}}"
fi

ESCAPED_SUMMARY=$(echo "$SUMMARY" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip())[1:-1])")
ESCAPED_SOURCE=$(echo "$SOURCE_LINE" | python3 -c "import sys,json; print(json.dumps(sys.stdin.read().strip())[1:-1])")

PAYLOAD=$(cat <<EOF
{
  "blocks": [
    {"type":"header","text":{"type":"plain_text","text":":rotating_light: Deploy Blocked — HIGH Risk Plan","emoji":true}},
    {"type":"section","text":{"type":"mrkdwn","text":"${ESCAPED_SOURCE}"}},
    {"type":"section","text":{"type":"mrkdwn","text":"*Summary*\n${ESCAPED_SUMMARY}"}}
    ${FINDINGS_BLOCK},
    {"type":"actions","elements":[{"type":"button","text":{"type":"plain_text","text":":unlock: Approve Override","emoji":true},"url":"${OVERRIDE_URL}","style":"danger"}]},
    {"type":"divider"},
    {"type":"context","elements":[{"type":"mrkdwn","text":"Risk: :red_circle: HIGH | Auto-apply blocked — board member override required"}]}
  ],
  "text": "Deploy Blocked — HIGH Risk Plan: ${REPO}"
}
EOF
)

curl -s -X POST -H "Content-Type: application/json" -d "$PAYLOAD" "$SLACK_WEBHOOK_URL" > /dev/null
echo "Slack notification sent: HIGH risk plan blocked"
