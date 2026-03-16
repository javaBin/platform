#!/bin/sh
# Run the LLM review script and extract the risk level.
#
# Usage: extract-review-risk.sh <review_script> <plan_file>
#
# Outputs to GITHUB_OUTPUT: risk_level
# Note: review-plan.py requires Python (Bedrock SDK), so Python is needed here.

set -e

REVIEW_SCRIPT="$1"
PLAN_FILE="$2"

python3 "$REVIEW_SCRIPT" "$PLAN_FILE" > review-output.txt 2>&1 || true
cat review-output.txt

if [ -f review-result.json ]; then
  RISK=$(jq -r '.risk // "FAILED"' review-result.json)
else
  RISK="FAILED"
fi

echo "risk_level=${RISK}" >> "$GITHUB_OUTPUT"
echo "LLM review risk: ${RISK}"

if [ "$RISK" = "FAILED" ]; then
  echo "LLM review failed — blocking pipeline."
  exit 1
fi
