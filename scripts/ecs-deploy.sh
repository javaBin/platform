#!/bin/sh
# Deploy a new container image to an ECS service.
#
# Forces a new deployment so ECS pulls the latest image for the current
# task definition. Does NOT create new task definition revisions — that's
# Terraform's job.
#
# If a deployment is already in progress (e.g. from tf-apply), skips the
# force and just waits for it to stabilize.
#
# Usage: ecs-deploy.sh
#
# Env: CLUSTER, SERVICE

set -e

# Check if a deployment is already rolling out
DEPLOYMENT_COUNT=$(aws ecs describe-services \
  --cluster "$CLUSTER" --services "$SERVICE" \
  --query 'length(services[0].deployments)' --output text)

if [ "$DEPLOYMENT_COUNT" -gt 1 ]; then
  echo "Deployment already in progress (${DEPLOYMENT_COUNT} deployments), waiting..."
else
  echo "Forcing new deployment for ${SERVICE} on ${CLUSTER}..."
  aws ecs update-service \
    --cluster "$CLUSTER" --service "$SERVICE" \
    --force-new-deployment > /dev/null
fi

echo "Waiting for service to stabilize..."
aws ecs wait services-stable --cluster "$CLUSTER" --services "$SERVICE"
echo "Deployment complete."
