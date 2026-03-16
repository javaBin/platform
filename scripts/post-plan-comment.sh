#!/bin/sh
# Post terraform plan + LLM review as a single PR comment.
#
# Usage: post-plan-comment.sh <plan-output-file> <has_changes>
#
# If review-output.txt exists in the current directory, it is appended
# to the comment. This avoids posting two separate comments.
#
# Env: GH_TOKEN (or gh auth), GITHUB_REPOSITORY, PR_NUMBER

set -e

PLAN_FILE="$1"
HAS_CHANGES="$2"

# --- Extract plan summary line (e.g. "Plan: 5 to add, 2 to change, 0 to destroy") ---
SUMMARY=$(grep -E '^(Plan:|No changes\.)' "$PLAN_FILE" 2>/dev/null | head -1)
[ -z "$SUMMARY" ] && SUMMARY="See plan output below."

if [ "$HAS_CHANGES" = "true" ]; then
  STATUS=":construction: **Changes detected** — ${SUMMARY}"
else
  STATUS=":white_check_mark: **No changes** — infrastructure is up to date."
fi

# --- Filter plan: strip refresh/read noise, keep only the meaningful diff ---
PLAN_CLEAN_FILE="$(dirname "$PLAN_FILE")/plan-clean.txt"
if [ -f "$PLAN_CLEAN_FILE" ]; then
  PLAN=$(head -c 60000 "$PLAN_CLEAN_FILE")
else
  # Fallback: filter inline
  PLAN=$(grep -v -E '^(module\.|data\.|\s+#).*: (Refreshing state|Reading|Read complete)' "$PLAN_FILE" 2>/dev/null | head -c 60000)
fi

# --- Build comment ---
cat > /tmp/plan-comment.md <<ENDOFCOMMENT
## Terraform Plan

${STATUS}

<details><summary>Plan output</summary>

\`\`\`
${PLAN}
\`\`\`

</details>
ENDOFCOMMENT

# --- Append LLM review if available ---
if [ -f review-output.txt ]; then
  REVIEW=$(cat review-output.txt)
  cat >> /tmp/plan-comment.md <<ENDOFREVIEW

---

### LLM Review

${REVIEW}
ENDOFREVIEW
fi

gh pr comment "$PR_NUMBER" --repo "$GITHUB_REPOSITORY" --body-file /tmp/plan-comment.md
