#!/bin/sh
# Queue a team-provisioner sync_team_access run (async). Used by platform-ci
# after apply: a new team's guardrails policy only exists after this apply, so
# the registry's team sync could not grant the team its AWS access yet.
set -eu
CI_CONTEXT="{}"
if [ -n "${GITHUB_ACTOR:-}" ]; then
  CI_CONTEXT=$(printf '{"actor":"%s","repo":"%s","sha":"%s","run_id":"%s"}' \
    "${GITHUB_ACTOR}" "${GITHUB_REPOSITORY}" "${GITHUB_SHA}" "${GITHUB_RUN_ID}")
fi
aws lambda invoke \
  --function-name "${PROJECT:-javabin}-team-provisioner" \
  --invocation-type Event \
  --cli-binary-format raw-in-base64-out \
  --payload "{\"action\":\"sync_team_access\",\"ci_context\":${CI_CONTEXT}}" \
  /dev/null
echo "Queued team-provisioner sync_team_access"
