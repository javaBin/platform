#!/bin/sh
# Verify a Terraform plan file's SHA256 hash.
#
# Usage: verify-plan.sh <plan_file> <expected_sha256>

set -e

PLAN_FILE="$1"
EXPECTED="$2"

ACTUAL=$(sha256sum "$PLAN_FILE" | awk '{print $1}')

if [ "$EXPECTED" != "$ACTUAL" ]; then
  echo "Plan SHA256 mismatch! Expected: ${EXPECTED}, Got: ${ACTUAL}"
  exit 1
fi

echo "Plan integrity verified."
