#!/bin/sh
# Deploy a new container image to an ECS service.
#
# Usage: ecs-deploy.sh
#
# Env: CLUSTER, SERVICE, ECR_REPO, IMAGE_TAG, ACCOUNT_ID, REGION

set -e

SCRIPT_DIR=$(dirname "$0")
export ECR_URI="${ACCOUNT_ID}.dkr.ecr.${REGION}.amazonaws.com/${ECR_REPO}:${IMAGE_TAG}"

TASK_DEF_ARN=$(aws ecs describe-services \
  --cluster "$CLUSTER" --services "$SERVICE" \
  --query 'services[0].taskDefinition' --output text)

aws ecs describe-task-definition --task-definition "$TASK_DEF_ARN" \
  --query 'taskDefinition' > task-def.json

sh "$SCRIPT_DIR/update-task-def.sh" task-def.json task-def-new.json

NEW_ARN=$(aws ecs register-task-definition \
  --cli-input-json file://task-def-new.json \
  --query 'taskDefinition.taskDefinitionArn' --output text)
echo "New task definition: $NEW_ARN"

aws ecs update-service \
  --cluster "$CLUSTER" --service "$SERVICE" \
  --task-definition "$NEW_ARN" > /dev/null

echo "Waiting for service to stabilize..."
aws ecs wait services-stable --cluster "$CLUSTER" --services "$SERVICE"
echo "Deployment complete."
