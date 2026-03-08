#!/bin/sh
# Post LLM review result as a PR comment.
#
# Usage: post-review-comment.sh
#
# Reads review-output.txt from current directory.
# Env: GH_TOKEN (or gh auth), GITHUB_REPOSITORY, PR_NUMBER

set -e

REVIEW=$(cat review-output.txt 2>/dev/null || echo "LLM review output not available.")

cat > /tmp/review-comment.md <<EOF
## LLM Plan Review

${REVIEW}
EOF

gh pr comment "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --body-file /tmp/review-comment.md
