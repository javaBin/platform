#!/bin/sh
# Block apply if risk is HIGH or FAILED.
#
# Usage: check-risk-block.sh <risk_level>
#
# Exits 0 if safe to apply, 1 if blocked.
# Slack notification is handled by notify-high-risk.sh in the review step —
# no duplicate alert here.

set -e

RISK="$1"

echo "LLM review risk: ${RISK}"

if [ "$RISK" != "HIGH" ] && [ "$RISK" != "FAILED" ] && [ -n "$RISK" ]; then
  exit 0
fi

echo "Auto-apply blocked (risk=${RISK})."
exit 1
