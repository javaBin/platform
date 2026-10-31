#!/bin/sh
# Tell the developer how to get a blocked apply through (invoke-apply-gate.sh).
#
# Usage: override-help.sh <risk_level> <plan_key>
#
# HIGH:   the review found HIGH risk; a platform team member must approve an
#         override, then the developer re-runs the failed jobs.
# FAILED: the review service gave no verdict (not a risk finding); re-running
#         all jobs re-plans and re-reviews. "Re-run failed jobs" would only
#         re-read the FAILED verdict. An override also works.
# Other risk levels (or none): nothing to add.
#
# Writes an ::error annotation and the same text to the job summary.
# Env: GITHUB_SERVER_URL, GITHUB_REPOSITORY, GITHUB_RUN_ID, GITHUB_STEP_SUMMARY

RISK="$1"
PLAN_KEY="$2"
SERVER="${GITHUB_SERVER_URL:-https://github.com}"
RUN_URL="${SERVER}/${GITHUB_REPOSITORY}/actions/runs/${GITHUB_RUN_ID}"
DOC="${SERVER}/javaBin/platform/blob/main/docs/apply-gate.md#requesting-an-override"
APPROVE="${SERVER}/javaBin/platform/actions/workflows/approve-override.yml"

case "$RISK" in
  HIGH)
    TITLE="Apply blocked — the plan review found HIGH risk"
    WHY="Read the review in the Terraform Plan job (or the PR comment). If the change is intended, ask the platform team (GitHub team javaBin/platform) to approve an override."
    AFTER="When the override is approved, open ${RUN_URL} and click \"Re-run failed jobs\"."
    ;;
  FAILED)
    TITLE="Apply blocked — the plan-review service failed (no risk verdict)"
    WHY="This is a service failure, not a risk finding. Open ${RUN_URL} and click \"Re-run all jobs\" to re-plan and re-review (\"Re-run failed jobs\" reuses the FAILED verdict). If the review keeps failing, ask the platform team (GitHub team javaBin/platform) for an override."
    AFTER="After an override, click \"Re-run failed jobs\" instead."
    ;;
  *)
    exit 0
    ;;
esac

ASK="Send them the plan key \`${PLAN_KEY}\` and this run (${RUN_URL}). They run ${APPROVE} with plan_key=${PLAN_KEY} and a reason, or: gh workflow run approve-override.yml -R javaBin/platform -f plan_key=${PLAN_KEY} -f reason=\"...\""

echo "::error title=${TITLE}::${WHY} ${AFTER} Details: ${DOC}"
echo "$ASK"
{
  echo "### ${TITLE}"
  echo
  echo "$WHY"
  echo
  echo "$ASK"
  echo
  echo "$AFTER See ${DOC}"
} >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
