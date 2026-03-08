#!/bin/sh
# Run terraform plan and detect drift. Notify Slack if drift found.
#
# Usage: drift-check.sh <ssm_param> <run_url>
#
# Must be run from the terraform root directory.

set -e

SSM_PARAM="$1"
RUN_URL="$2"
SCRIPT_DIR=$(dirname "$0")

set +e
terraform plan -detailed-exitcode -no-color -lock-timeout=5m > drift.txt 2>&1
EXIT_CODE=$?
set -e

if [ "$EXIT_CODE" = "2" ]; then
  echo "Drift detected!"
  export SSM_WEBHOOK_PARAM="$SSM_PARAM"
  sh "$SCRIPT_DIR/notify-slack.sh" \
    "Terraform Drift Detected" \
    "Someone made changes outside of Terraform." \
    "$RUN_URL" \
    "View Full Plan"
  exit 1
elif [ "$EXIT_CODE" = "1" ]; then
  echo "Terraform plan failed during drift detection."
  exit 1
else
  echo "No drift detected."
fi
