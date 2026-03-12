#!/bin/sh
# Deploy a new container image to an ECS service.
#
# Forces a new deployment so ECS pulls the latest image for the current
# task definition. Does NOT create new task definition revisions — that's
# Terraform's job. This avoids drift between TF-managed task defs
# (with security hardening, volumes, etc.) and deploy-time revisions.
#
# Usage: ecs-deploy.sh
#
# Env: CLUSTER, SERVICE

set -e

echo "Forcing new deployment for ${SERVICE} on ${CLUSTER}..."
aws ecs update-service \
  --cluster "$CLUSTER" --service "$SERVICE" \
  --force-new-deployment > /dev/null

echo "Waiting for service to stabilize..."
aws ecs wait services-stable --cluster "$CLUSTER" --services "$SERVICE"
echo "Deployment complete."
