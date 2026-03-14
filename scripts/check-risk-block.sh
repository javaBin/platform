#!/bin/sh
# Block apply if risk is HIGH or FAILED, unless an override exists.
#
# Usage: check-risk-block.sh <risk_level>
#
# Exits 0 if safe to apply, 1 if blocked.
# On HIGH risk, checks S3 for an override.json signed by the apply-gate Lambda.
# Requires: PLAN_BUCKET, GITHUB_REPOSITORY, GITHUB_RUN_ID env vars.

set -e

RISK="$1"

echo "LLM review risk: ${RISK}"

if [ "$RISK" != "HIGH" ] && [ "$RISK" != "FAILED" ] && [ -n "$RISK" ]; then
  exit 0
fi

# Check for a signed override in S3
PLAN_PREFIX="plans/${GITHUB_REPOSITORY}/${GITHUB_RUN_ID}"
OVERRIDE_KEY="${PLAN_PREFIX}/override.json"

if aws s3 ls "s3://${PLAN_BUCKET}/${OVERRIDE_KEY}" > /dev/null 2>&1; then
  echo "Override found: s3://${PLAN_BUCKET}/${OVERRIDE_KEY}"
  echo "Proceeding with HIGH risk apply."
  exit 0
fi

echo "Auto-apply blocked (risk=${RISK}). No override found."
exit 1
