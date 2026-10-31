#!/bin/sh
# Deploy the image built from this commit to an ECS service.
#
# Terraform owns the shape of the task definition (env, secrets, cpu, roles);
# this script owns which image runs. The ecs-service module ignores the
# service's task_definition and desired_count after creation, so:
#
#   1. Take the family's latest ACTIVE revision: Terraform's newest, or the
#      last revision this script registered from it.
#   2. Register a copy with the app container pinned to IMAGE (repo@digest),
#      so every task of the deployment runs exactly this commit's build.
#   3. Point the service at it, with the desired count from the service's
#      javabin:desired-count tag (Terraform writes it from app.yaml; a new
#      service is created with 0 tasks and gets them here).
#   4. Wait until that deployment's rolloutState is COMPLETED. A circuit-
#      breaker rollback, a FAILED rollout or a timeout fails the job after
#      printing why the tasks stopped.
#
# Usage: ecs-deploy.sh
#
# Env: CLUSTER, SERVICE, IMAGE (repo@sha256:... or repo:tag)
#      DEPLOY_TIMEOUT (seconds, default 1200), POLL_INTERVAL (seconds, default 15)

set -eu

: "${CLUSTER:?CLUSTER is required}"
: "${SERVICE:?SERVICE is required}"
: "${IMAGE:?IMAGE is required (repo@sha256:... from docker-build)}"
DEPLOY_TIMEOUT="${DEPLOY_TIMEOUT:-1200}"
POLL_INTERVAL="${POLL_INTERVAL:-15}"

TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
: > "$TMP/err"

# repo of an image reference: drop @digest, then a :tag after the last /
REPO_OF='def repo: sub("@.*$"; "") | sub(":[^:/]*$"; "");'

fail() {
  echo "::error::$1"
  exit 1
}

describe_service() {
  aws ecs describe-services --cluster "$CLUSTER" --services "$SERVICE" --output json > "$TMP/service.json"
}

# -- 1. Base revision --------------------------------------------------------

describe_service
SERVICE_ARN=$(jq -r '.services[0] | select(.status == "ACTIVE") | .serviceArn' "$TMP/service.json")
[ -n "$SERVICE_ARN" ] || fail "ECS service ${SERVICE} not found in ${CLUSTER}. tf-apply creates it from app.yaml."
FAMILY=$(jq -r '.services[0].taskDefinition | split("/")[-1] | split(":")[0]' "$TMP/service.json")

aws ecs describe-task-definition --task-definition "$FAMILY" --output json > "$TMP/base.json"
BASE_ARN=$(jq -r '.taskDefinition.taskDefinitionArn' "$TMP/base.json")
echo "Base revision: ${BASE_ARN}"

# -- 2. Register a revision pinned to IMAGE ---------------------------------

MATCHED=$(jq -r --arg image "$IMAGE" "$REPO_OF"'
  [.taskDefinition.containerDefinitions[] | select((.image | repo) == ($image | repo))] | length' "$TMP/base.json")
if [ "$MATCHED" -eq 0 ]; then
  IMAGES=$(jq -r '[.taskDefinition.containerDefinitions[].image] | join(", ")' "$TMP/base.json")
  fail "No container in ${BASE_ARN} uses the repository of ${IMAGE} (containers: ${IMAGES})"
fi

jq --arg image "$IMAGE" "$REPO_OF"'
  .taskDefinition
  | del(.taskDefinitionArn, .revision, .status, .requiresAttributes, .compatibilities,
        .registeredAt, .registeredBy, .deregisteredAt)
  | .containerDefinitions |= map(if (.image | repo) == ($image | repo) then .image = $image else . end)
' "$TMP/base.json" > "$TMP/register.json"

# Copy the base revision's tags (team, service, ...). Tag reads and tag-on-
# create need permissions older deploy roles lack: register untagged then.
if aws ecs list-tags-for-resource --resource-arn "$BASE_ARN" --output json > "$TMP/base-tags.json" 2>/dev/null &&
  [ "$(jq '.tags | length' "$TMP/base-tags.json")" -gt 0 ]; then
  jq --slurpfile t "$TMP/base-tags.json" '.tags = $t[0].tags' "$TMP/register.json" > "$TMP/register-tagged.json"
  if ! aws ecs register-task-definition --cli-input-json "file://$TMP/register-tagged.json" --output json > "$TMP/registered.json"; then
    echo "::warning::Could not register a tagged revision; registering it without tags"
    aws ecs register-task-definition --cli-input-json "file://$TMP/register.json" --output json > "$TMP/registered.json"
  fi
else
  aws ecs register-task-definition --cli-input-json "file://$TMP/register.json" --output json > "$TMP/registered.json"
fi
NEW_TD=$(jq -r '.taskDefinition.taskDefinitionArn' "$TMP/registered.json")
echo "Registered ${NEW_TD} with ${IMAGE}"

# -- 3. Update the service ---------------------------------------------------

DESIRED=""
if aws ecs list-tags-for-resource --resource-arn "$SERVICE_ARN" --output json > "$TMP/service-tags.json" 2>/dev/null; then
  DESIRED=$(jq -r '.tags[]? | select(.key == "javabin:desired-count") | .value' "$TMP/service-tags.json")
fi
case "$DESIRED" in
  "") echo "No javabin:desired-count tag on ${SERVICE}; keeping its desired count" ;;
  *[!0-9]*) fail "javabin:desired-count tag on ${SERVICE} is not a number: ${DESIRED}" ;;
esac

echo "Updating ${SERVICE} on ${CLUSTER}${DESIRED:+ (desired count ${DESIRED})}..."
if [ -n "$DESIRED" ]; then
  aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
    --task-definition "$NEW_TD" --desired-count "$DESIRED" --output json > "$TMP/update.json"
else
  aws ecs update-service --cluster "$CLUSTER" --service "$SERVICE" \
    --task-definition "$NEW_TD" --output json > "$TMP/update.json"
fi
DEPLOYMENT_ID=$(jq -r '.service.deployments[] | select(.status == "PRIMARY") | .id' "$TMP/update.json")
echo "Deployment ${DEPLOYMENT_ID} started"

# -- 4. Wait for the rollout -------------------------------------------------

print_diagnostics() {
  echo "Recent service events:"
  jq -r '.services[0].events[:10][] | "  \(.createdAt)  \(.message)"' "$TMP/service.json"

  echo "Stopped tasks of ${NEW_TD##*/}:"
  if aws ecs list-tasks --cluster "$CLUSTER" --service-name "$SERVICE" --desired-status STOPPED \
    --output json > "$TMP/stopped.json" 2>"$TMP/err" &&
    [ "$(jq '.taskArns | length' "$TMP/stopped.json")" -gt 0 ]; then
    # DescribeTasks takes at most 100 ARNs; ListTasks returns at most 100 per page
    # shellcheck disable=SC2046
    if aws ecs describe-tasks --cluster "$CLUSTER" --tasks $(jq -r '.taskArns[]' "$TMP/stopped.json") \
      --output json > "$TMP/tasks.json" 2>"$TMP/err"; then
      jq -r --arg td "$NEW_TD" '
        [.tasks[] | select(.taskDefinitionArn == $td)] | sort_by(.stoppedAt) | reverse | .[:5]
        | if length == 0 then "  (none found)" else .[] |
          "  task \(.taskArn | split("/")[-1]): \(.stopCode // "-"): \(.stoppedReason // "-")",
          (.containers[] | "    container \(.name): exit code \(.exitCode // "-"), \(.reason // "no reason given")")
          end' "$TMP/tasks.json"
    else
      echo "  (could not describe tasks: $(cat "$TMP/err"))"
    fi
  else
    echo "  (none listed$( [ -s "$TMP/err" ] && echo ": $(cat "$TMP/err")"))"
  fi

  jq -r '.taskDefinition.containerDefinitions[]
    | select(.logConfiguration.logDriver == "awslogs") | .logConfiguration.options
    | "Logs: log group \(."awslogs-group"), streams \(."awslogs-stream-prefix")/<container>/<task id>"' \
    "$TMP/registered.json"
}

DEADLINE=$(($(date +%s) + DEPLOY_TIMEOUT))
echo "Waiting for the rollout to complete (timeout ${DEPLOY_TIMEOUT}s)..."
while :; do
  describe_service
  PRIMARY_ID=$(jq -r '.services[0].deployments[] | select(.status == "PRIMARY") | .id' "$TMP/service.json")
  STATE=$(jq -r --arg id "$DEPLOYMENT_ID" '.services[0].deployments[] | select(.id == $id) | .rolloutState' "$TMP/service.json")
  REASON=$(jq -r --arg id "$DEPLOYMENT_ID" '.services[0].deployments[] | select(.id == $id) | .rolloutStateReason // ""' "$TMP/service.json")
  PRIMARY_TD=$(jq -r '.services[0].deployments[] | select(.status == "PRIMARY") | .taskDefinition' "$TMP/service.json")

  if [ "$PRIMARY_ID" != "$DEPLOYMENT_ID" ]; then
    # A newer revision of the family: a later run deployed over this one
    if [ "${PRIMARY_TD%:*}" = "${NEW_TD%:*}" ] && [ "${PRIMARY_TD##*:}" -gt "${NEW_TD##*:}" ]; then
      echo "::warning::Deployment ${DEPLOYMENT_ID} was superseded by a newer deploy (${PRIMARY_TD##*/}); its run reports the result."
      exit 0
    fi
    RESULT="was replaced by deployment ${PRIMARY_ID} (${PRIMARY_TD##*/})${REASON:+: ${REASON}}"
    break
  fi
  if [ "$STATE" = "COMPLETED" ]; then
    [ "$PRIMARY_TD" = "$NEW_TD" ] || fail "Deployment ${DEPLOYMENT_ID} completed with ${PRIMARY_TD}, expected ${NEW_TD}"
    RUNNING=$(jq -r '.services[0].deployments[] | select(.status == "PRIMARY") | "\(.runningCount)/\(.desiredCount)"' "$TMP/service.json")
    echo "Deployment complete: ${NEW_TD##*/} (${IMAGE}), ${RUNNING} tasks running."
    exit 0
  fi
  if [ "$STATE" = "FAILED" ]; then
    RESULT="failed${REASON:+: ${REASON}}"
    break
  fi
  if [ "$(date +%s)" -ge "$DEADLINE" ]; then
    RESULT="did not complete within ${DEPLOY_TIMEOUT}s (rolloutState ${STATE})"
    break
  fi
  sleep "$POLL_INTERVAL"
done

print_diagnostics
fail "Deployment ${DEPLOYMENT_ID} of ${IMAGE} to ${SERVICE} ${RESULT}"
