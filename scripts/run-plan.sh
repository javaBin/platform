#!/usr/bin/env bash
# Run terraform plan with detailed exit code handling.
#
# Usage:
#   scripts/run-plan.sh <working-directory> [extra-args...]
#
# Exit codes from terraform plan -detailed-exitcode:
#   0 = no changes
#   1 = error
#   2 = changes present
#
# Outputs (written to $GITHUB_OUTPUT if set):
#   has_changes=true|false
#
# The plan binary is written to <working-directory>/tfplan
# Human-readable output is written to <working-directory>/plan-output.txt

set -uo pipefail

WORK_DIR="${1:?Usage: run-plan.sh <working-directory> [extra-args...]}"
shift
EXTRA_ARGS=("$@")

cd "$WORK_DIR"

set +e
terraform plan -out=tfplan -detailed-exitcode -no-color "${EXTRA_ARGS[@]}" > plan-output.txt 2>&1
PLAN_EXIT=$?
set -e

cat plan-output.txt

if [ "$PLAN_EXIT" = "1" ]; then
  echo "Terraform plan failed"
  exit 1
elif [ "$PLAN_EXIT" = "2" ]; then
  echo "has_changes=true" >> "${GITHUB_OUTPUT:-/dev/null}"
else
  echo "has_changes=false" >> "${GITHUB_OUTPUT:-/dev/null}"
  echo "No changes — downstream jobs will skip."
fi
