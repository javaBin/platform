#!/bin/sh
# Decide whether this job can reach AWS via GitHub OIDC.
#
# Usage: check-aws-access.sh
#
# GitHub only exposes ACTIONS_ID_TOKEN_REQUEST_URL to jobs that were granted
# `id-token: write`. Pull requests from forks never are, and neither is a
# Dependabot PR unless deploy.yml grants it. Those runs skip every AWS step
# with a notice instead of failing on the OIDC token.
#
# Output: available=true|false written to GITHUB_OUTPUT

set -e

if [ -n "${ACTIONS_ID_TOKEN_REQUEST_URL:-}" ]; then
  echo "available=true" >> "${GITHUB_OUTPUT:-/dev/null}"
  exit 0
fi

echo "::notice title=Terraform plan skipped::This run has no GitHub OIDC token (pull request from a fork, or a Dependabot PR), so it cannot reach AWS. Terraform is validated and format-checked only; the plan runs after merge, or on a PR from a branch in this repository."
echo "available=false" >> "${GITHUB_OUTPUT:-/dev/null}"
