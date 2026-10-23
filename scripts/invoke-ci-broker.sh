#!/bin/sh
# Invoke the CI broker Lambda to get team-scoped credentials.
#
# Usage: invoke-ci-broker.sh <action>
#   action: "plan" or "deploy"
#
# Env: GITHUB_REPOSITORY (org/repo)
#      APP_TEAM (optional) — team from app.yaml; the broker verifies the repo
#      belongs to it instead of picking the repo's first GitHub team
# Output: Exports AWS_ACCESS_KEY_ID, AWS_SECRET_ACCESS_KEY, AWS_SESSION_TOKEN
#         Sets team={slug} in GITHUB_OUTPUT

set -e

ACTION="${1:?Usage: invoke-ci-broker.sh <plan|deploy>}"
REPO="${GITHUB_REPOSITORY##*/}"

echo "Requesting ${ACTION} credentials for ${REPO}..."

RESPONSE=$(aws lambda invoke \
  --function-name "${PROJECT:-javabin}-ci-broker" \
  --payload "$(jq -nc --arg repo "$REPO" --arg action "$ACTION" --arg session_name "${SESSION_NAME:-}" --arg team "${APP_TEAM:-}" \
    '{repo: $repo, action: $action, session_name: $session_name, team: $team}')" \
  --cli-binary-format raw-in-base64-out \
  /tmp/broker-response.json 2>&1)

RESULT=$(cat /tmp/broker-response.json)
APPROVED=$(echo "$RESULT" | jq -r '.approved // false')

if [ "$APPROVED" != "true" ]; then
  ERROR=$(echo "$RESULT" | jq -r '.error // "Unknown error"')
  echo "ERROR: CI broker rejected request: $ERROR"
  exit 1
fi

TEAM=$(echo "$RESULT" | jq -r '.team')
echo "Team: $TEAM"
echo "team=${TEAM}" >> "${GITHUB_OUTPUT:-/dev/null}"

# Export credentials for subsequent steps
{
  echo "AWS_ACCESS_KEY_ID=$(echo "$RESULT" | jq -r '.credentials.AccessKeyId')"
  echo "AWS_SECRET_ACCESS_KEY=$(echo "$RESULT" | jq -r '.credentials.SecretAccessKey')"
  echo "AWS_SESSION_TOKEN=$(echo "$RESULT" | jq -r '.credentials.SessionToken')"
} >> "${GITHUB_ENV:-/dev/null}"

# Mask credentials in logs
echo "::add-mask::$(echo "$RESULT" | jq -r '.credentials.AccessKeyId')"
echo "::add-mask::$(echo "$RESULT" | jq -r '.credentials.SecretAccessKey')"
echo "::add-mask::$(echo "$RESULT" | jq -r '.credentials.SessionToken')"

echo "Credentials issued for team=${TEAM} action=${ACTION}"
