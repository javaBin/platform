#!/bin/sh
# Resolve and retrigger the blocked run an override is for (approve-override.yml).
#
# Usage: override-target.sh resolve
#        override-target.sh retrigger
#
# resolve:   reads the repo and run ID from PLAN_KEY
#            (plans/<org>/<repo>/<run_id>/tfplan, as written by upload-plan.sh).
#            REPO and RUN_ID are optional; if given they must match the plan.
#            Writes repo, run_id and run_url to GITHUB_OUTPUT. Run it before
#            signing, so a typo never signs an override for the wrong plan.
# retrigger: re-runs the failed jobs of the blocked run when it is in this
#            repo (GH_TOKEN is this workflow's github.token, which cannot
#            touch other repos). For an app repo it prints, and writes to the
#            job summary, the step the developer takes instead: open the run
#            and click "Re-run failed jobs". The apply gate checks for the
#            override every time it runs, so a plain re-run is enough.
#
# Env: PLAN_KEY, REPO, RUN_ID, GITHUB_REPOSITORY, GITHUB_SERVER_URL,
#      GITHUB_OUTPUT, GITHUB_STEP_SUMMARY, GH_TOKEN (retrigger)

set -e

SERVER="${GITHUB_SERVER_URL:-https://github.com}"

case "$1" in
  resolve)
    # plans/<org>/<repo>/<run_id>/tfplan; names are GitHub-safe characters only
    PARSED=$(printf '%s\n' "$PLAN_KEY" |
      sed -n 's#^plans/\([A-Za-z0-9_.-]*\)/\([A-Za-z0-9_.-]*\)/\([0-9][0-9]*\)/tfplan$#\1/\2 \3#p')
    if [ -z "$PARSED" ]; then
      echo "::error::plan_key '${PLAN_KEY}' is not plans/<org>/<repo>/<run_id>/tfplan. Copy it from the blocked run's Terraform Apply log or the Slack alert."
      exit 1
    fi
    PLAN_REPO="${PARSED% *}"
    PLAN_RUN="${PARSED#* }"
    if [ -n "$REPO" ] && [ "$REPO" != "$PLAN_REPO" ]; then
      echo "::error::repo '${REPO}' does not match the plan's repo '${PLAN_REPO}'."
      exit 1
    fi
    if [ -n "$RUN_ID" ] && [ "$RUN_ID" != "$PLAN_RUN" ]; then
      echo "::error::run_id '${RUN_ID}' does not match the plan's run '${PLAN_RUN}'."
      exit 1
    fi
    {
      echo "repo=${PLAN_REPO}"
      echo "run_id=${PLAN_RUN}"
      echo "run_url=${SERVER}/${PLAN_REPO}/actions/runs/${PLAN_RUN}"
    } >> "${GITHUB_OUTPUT:-/dev/null}"
    echo "Override target: ${PLAN_REPO} run ${PLAN_RUN}"
    ;;

  retrigger)
    RUN_URL="${SERVER}/${REPO}/actions/runs/${RUN_ID}"
    if [ "$REPO" = "$GITHUB_REPOSITORY" ]; then
      gh run rerun "$RUN_ID" --repo "$REPO" --failed
      MSG="Override signed. Re-running the failed jobs of ${RUN_URL}"
    else
      MSG="Override signed. Next step for the developer: open ${RUN_URL} and click \"Re-run failed jobs\". Terraform Apply will find the override and apply; the image build and ECS deploy follow."
      echo "::notice title=Re-run the blocked apply::${MSG}"
    fi
    echo "$MSG"
    echo "$MSG" >> "${GITHUB_STEP_SUMMARY:-/dev/null}"
    ;;

  *)
    echo "Usage: override-target.sh resolve|retrigger" >&2
    exit 2
    ;;
esac
