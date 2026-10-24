#!/bin/sh
# Queue an app-auth-sync reconcile (async: a busy Lambda queues it instead of
# throttling). Used by platform-ci after apply.
set -eu
aws lambda invoke \
  --function-name "${PROJECT:-javabin}-app-auth-sync" \
  --invocation-type Event \
  --cli-binary-format raw-in-base64-out \
  --payload '{"action":"reconcile"}' \
  /dev/null
echo "Queued app-auth-sync reconcile"
