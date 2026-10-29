#!/bin/sh
# Notify Slack that an apply is blocked (risk HIGH, or the LLM review FAILED),
# with the review findings, plan key and override link.
#
# Usage: notify-high-risk.sh <ssm_param> <override_url>
#
# Reads review-result.json from the current directory and repo context from the
# GitHub Actions env (GITHUB_REPOSITORY, GITHUB_SHA, GITHUB_ACTOR, GITHUB_RUN_ID).
# The payload is built and checked by slack_post.py: Slack's reply is verified
# and a rejected block message falls back to plain text. A failed notification
# is reported as a CI error annotation but never fails the job.

set -e

SSM_PARAM="$1"
OVERRIDE_URL="$2"
SCRIPT_DIR=$(cd "$(dirname "$0")" && pwd)

SLACK_WEBHOOK_URL=$(aws ssm get-parameter \
  --name "$SSM_PARAM" \
  --with-decryption --query Parameter.Value --output text 2>/dev/null || echo "")
export SLACK_WEBHOOK_URL

python3 "$SCRIPT_DIR/slack_post.py" risk review-result.json "$OVERRIDE_URL" ||
  echo "::error::Blocked-apply Slack notification was not delivered (see above)."
