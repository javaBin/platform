#!/bin/sh
# Post LLM review result as a PR comment.
#
# Usage: post-review-comment.sh
#
# Reads review-result.json and review-output.txt from current directory.
# Env: GH_TOKEN (or gh auth), GITHUB_REPOSITORY, PR_NUMBER

set -e

RISK=$(jq -r '.risk // "FAILED"' review-result.json 2>/dev/null || echo "FAILED")
REVIEW=$(cat review-output.txt 2>/dev/null || echo "LLM review output not available.")

case "$RISK" in
  LOW)    EMOJI="🟢" ;;
  MEDIUM) EMOJI="🟡" ;;
  HIGH)   EMOJI="🔴" ;;
  *)      EMOJI="⚪" ;;
esac

cat > /tmp/review-comment.md <<EOF
## LLM Plan Review

**Risk: ${EMOJI} ${RISK}**

${REVIEW}
EOF

gh pr comment "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --body-file /tmp/review-comment.md
