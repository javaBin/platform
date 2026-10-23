"""App secret rollout — restart an app's ECS service when one of its secrets changes.

ECS resolves a task's `secrets` only when the task starts, so a value set
with `javabin secret set` reaches the app only after a new deployment.
Teams may write their own secret values but have no ecs:UpdateService (it
would also let them change networking or task definitions), so the
platform does the restart:

  SSM "Parameter Store Change" Update on /javabin/apps/{team}/{app}/{name}
    -> force a new deployment of service {team}-{app} in the platform cluster

Platform-managed parameters (auth request, Cognito credentials, RDS master
password) are ignored: their consumers redeploy through their own paths.
"""

import logging
import os
import re

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

PROJECT = os.environ.get("PROJECT", "javabin")
CLUSTER = os.environ.get("CLUSTER", f"{PROJECT}-platform")
SECRET_RE = re.compile(rf"^/{re.escape(PROJECT)}/apps/(?P<team>[a-z]{{1,12}})/(?P<app>[a-z][a-z0-9-]{{0,19}})/(?P<name>[^/]+)$")
PLATFORM_MANAGED = re.compile(r"^(auth|cognito-client-.*|db-master-password)$")

ecs = boto3.client("ecs")


def handler(event, context):
    detail = event.get("detail") or {}
    name, operation = detail.get("name", ""), detail.get("operation", "")
    m = SECRET_RE.match(name)
    if operation != "Update" or not m or PLATFORM_MANAGED.match(m["name"]):
        return {"status": "ignored"}

    service = f"{m['team']}-{m['app']}"
    try:
        ecs.update_service(cluster=CLUSTER, service=service, forceNewDeployment=True)
    except (ecs.exceptions.ServiceNotFoundException, ecs.exceptions.ServiceNotActiveException):
        logger.info("%s changed but service %s is not running; nothing to restart", name, service)
        return {"status": "no-service", "service": service}
    logger.info("%s changed: restarting %s", name, service)
    return {"status": "restarted", "service": service}
