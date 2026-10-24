#!/bin/sh
# Print a GitHub Actions OIDC token for the given audience.
#
# Usage: github-oidc-token.sh <audience>
#
# The job needs `permissions: id-token: write`. The platform Lambdas
# (ci-broker, apply-gate) verify the token and read the repo from its signed
# claims, so a job can only ever act as its own repo.

set -e

AUDIENCE="${1:?Usage: github-oidc-token.sh <audience>}"
: "${ACTIONS_ID_TOKEN_REQUEST_URL:?id-token: write permission is required}"

RESPONSE=$(curl -sSf -H "Authorization: bearer ${ACTIONS_ID_TOKEN_REQUEST_TOKEN}" \
  "${ACTIONS_ID_TOKEN_REQUEST_URL}&audience=${AUDIENCE}")
TOKEN=$(echo "$RESPONSE" | jq -r '.value // empty')
if [ -z "$TOKEN" ]; then
  echo "ERROR: no OIDC token returned for audience ${AUDIENCE}" >&2
  exit 1
fi
echo "$TOKEN"
