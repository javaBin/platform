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
# GitHub caps a comment at 65,536 characters, so the plan shown here is cut at
# PLAN_COMMENT_CHARS. Say so when it is: readers (and anyone pasting the
# comment into a model) must not mistake the cut for the whole plan. The LLM
# review reads plan-output.txt itself and is not affected by this cut.
PLAN_COMMENT_CHARS="${PLAN_COMMENT_CHARS:-60000}"
PLAN_CLEAN_FILE="$(dirname "$PLAN_FILE")/plan-clean.txt"
if [ ! -f "$PLAN_CLEAN_FILE" ]; then
  # Fallback: filter inline
  PLAN_CLEAN_FILE=$(mktemp)
  trap 'rm -f "$PLAN_CLEAN_FILE"' EXIT
  grep -v -E '^(module\.|data\.|\s+#).*: (Refreshing state|Reading|Read complete)' "$PLAN_FILE" > "$PLAN_CLEAN_FILE" 2>/dev/null || true
fi
PLAN=$(head -c "$PLAN_COMMENT_CHARS" "$PLAN_CLEAN_FILE")
PLAN_SIZE=$(wc -c < "$PLAN_CLEAN_FILE" | tr -d ' ')
CUT_NOTE=""
if [ "$PLAN_SIZE" -gt "$PLAN_COMMENT_CHARS" ]; then
  CUT_NOTE="> :scissors: Plan output above is cut at ${PLAN_COMMENT_CHARS} of ${PLAN_SIZE} characters (GitHub comment size limit). The full plan is in the workflow log; the LLM review below is not affected by this cut."
fi

# --- Build comment ---
cat > /tmp/plan-comment.md <<ENDOFCOMMENT
## Terraform Plan

${STATUS}

<details><summary>Plan output</summary>

\`\`\`
${PLAN}
\`\`\`

${CUT_NOTE}

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
