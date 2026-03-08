#!/bin/sh
# Update an ECS task definition with a new container image.
#
# Usage: update-task-def.sh <input.json> <output.json>
#
# Env: ECR_URI — full image URI with tag

set -e

INPUT="$1"
OUTPUT="$2"

if [ -z "$ECR_URI" ]; then
  echo "ECR_URI is required" >&2
  exit 1
fi

jq --arg uri "$ECR_URI" '
  .containerDefinitions[0].image = $uri |
  del(.taskDefinitionArn, .revision, .status, .requiresAttributes,
      .compatibilities, .registeredAt, .registeredBy, .deregisteredAt)
' "$INPUT" > "$OUTPUT"

echo "Updated task definition: $OUTPUT"
