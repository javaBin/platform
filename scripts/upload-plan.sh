#!/usr/bin/env bash
# Upload terraform plan + output to S3 and set GitHub outputs.
#
# Usage:
#   scripts/upload-plan.sh <working-directory>
#
# Required environment variables:
#   PLAN_BUCKET       - S3 bucket for plan artifacts
#   GITHUB_REPOSITORY - e.g. javaBin/platform
#   GITHUB_RUN_ID     - workflow run ID
#
# Outputs (written to $GITHUB_OUTPUT):
#   plan_key       - S3 key for the plan binary
#   plan_text_key  - S3 key for the plan text output
#   plan_sha256    - SHA256 hash of the plan binary

set -euo pipefail

WORK_DIR="${1:?Usage: upload-plan.sh <working-directory>}"

cd "$WORK_DIR"

PREFIX="plans/${GITHUB_REPOSITORY}/${GITHUB_RUN_ID}"
SHA256=$(sha256sum tfplan | awk '{print $1}')

aws s3 cp tfplan "s3://${PLAN_BUCKET}/${PREFIX}/tfplan"
aws s3 cp plan-output.txt "s3://${PLAN_BUCKET}/${PREFIX}/plan-output.txt"

{
  echo "plan_key=${PREFIX}/tfplan"
  echo "plan_text_key=${PREFIX}/plan-output.txt"
  echo "plan_sha256=${SHA256}"
} >> "${GITHUB_OUTPUT:-/dev/null}"
