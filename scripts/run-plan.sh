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
# A filtered version (no refresh noise) is written to <working-directory>/plan-clean.txt

set -uo pipefail

WORK_DIR="${1:?Usage: run-plan.sh <working-directory> [extra-args...]}"
shift
EXTRA_ARGS=("$@")

cd "$WORK_DIR"

set +e
terraform plan -out=tfplan -detailed-exitcode -no-color "${EXTRA_ARGS[@]}" > plan-output.txt 2>&1
PLAN_EXIT=$?
set -e

# Create filtered output: strip state refresh and data source read lines
grep -v -E '^(module\.|data\.|\s+#).*: (Refreshing state|Reading|Read complete)' plan-output.txt > plan-clean.txt || true

# Print only the clean output to the CI log (not hundreds of refresh lines)
cat plan-clean.txt

if [ "$PLAN_EXIT" = "1" ]; then
  echo "Terraform plan failed"
  exit 1
elif [ "$PLAN_EXIT" = "2" ]; then
  echo "has_changes=true" >> "${GITHUB_OUTPUT:-/dev/null}"
else
  echo "has_changes=false" >> "${GITHUB_OUTPUT:-/dev/null}"
  echo "No changes — downstream jobs will skip."
fi
