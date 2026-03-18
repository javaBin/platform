"""CI credential broker — validates repo→team membership via GitHub API
and returns temporary STS credentials for the team-scoped IAM role.

This Lambda is the enforcement point for team-based access control.
CI workflows assume a lightweight broker role (can only invoke this Lambda),
then receive team-scoped credentials if the repo belongs to a registered team.

Actions:
  - plan:   Returns credentials for javabin-ci-team-{team} (Terraform plan/apply)
  - deploy: Returns credentials for javabin-ci-deploy-{team} (Docker build/ECS deploy)
"""

import json
import logging
import os

import boto3
from shared.github import resolve_team

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

sts = boto3.client("sts")

ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "")
PROJECT = os.environ.get("PROJECT", "javabin")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "javaBin")

PLAN_DURATION = 3600   # 1 hour for plan
DEPLOY_DURATION = 900  # 15 minutes for deploy

ROLE_MAP = {
    "plan": f"{PROJECT}-ci-team-",
    "deploy": f"{PROJECT}-ci-deploy-",
}


def _assume_role(role_arn, session_name, duration):
    """Assume an IAM role and return temporary credentials."""
    resp = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
        DurationSeconds=duration,
    )
    creds = resp["Credentials"]
    return {
        "AccessKeyId": creds["AccessKeyId"],
        "SecretAccessKey": creds["SecretAccessKey"],
        "SessionToken": creds["SessionToken"],
    }


def handler(event, context):
    repo = event.get("repo", "")
    action = event.get("action", "plan")

    if not repo:
        return {"error": "repo is required", "approved": False}

    if action not in ROLE_MAP:
        return {"error": f"Invalid action: {action}. Must be plan or deploy", "approved": False}

    # Resolve team from GitHub
    team = resolve_team(repo)
    if not team:
        logger.warning("Repo %s does not belong to any team", repo)
        return {
            "approved": False,
            "error": f"Repo '{repo}' is not in any GitHub team. "
                     f"Add it at https://github.com/orgs/{GITHUB_ORG}/teams",
        }

    # Assume the team-scoped role
    role_prefix = ROLE_MAP[action]
    role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_prefix}{team}"
    duration = PLAN_DURATION if action == "plan" else DEPLOY_DURATION

    try:
        credentials = _assume_role(role_arn, f"ci-broker-{repo}", duration)
    except Exception as e:
        logger.error("Failed to assume %s: %s", role_arn, e)
        return {
            "approved": False,
            "error": f"Failed to assume role for team '{team}': {e}",
        }

    logger.info("Issued %s credentials for repo=%s team=%s role=%s", action, repo, team, role_arn)
    return {
        "approved": True,
        "team": team,
        "credentials": credentials,
    }
