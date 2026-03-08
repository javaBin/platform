#!/bin/sh
# Post terraform plan output as a PR comment.
#
# Usage: post-plan-comment.sh <plan-output-file> <has_changes>
#
# Env: GH_TOKEN (or gh auth), GITHUB_REPOSITORY, PR_NUMBER

set -e

PLAN_FILE="$1"
HAS_CHANGES="$2"

if [ "$HAS_CHANGES" = "true" ]; then
  STATUS="**Changes detected** — review required."
else
  STATUS="**No changes** — infrastructure is up to date."
fi

PLAN=$(head -c 60000 "$PLAN_FILE" 2>/dev/null || echo "Plan output not available.")

cat > /tmp/plan-comment.md <<EOF
## Terraform Plan

${STATUS}

<details><summary>Plan output</summary>

\`\`\`
${PLAN}
\`\`\`

</details>
EOF

gh pr comment "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --body-file /tmp/plan-comment.md
