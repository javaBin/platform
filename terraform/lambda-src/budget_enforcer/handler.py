"""Budget enforcer — scales ECS services to zero when a team exceeds 200% of budget.

Trigger: SNS notification from AWS Budgets (200% threshold).
Parses budget name to extract team, finds ECS services tagged with that team,
scales them to desired_count=0, and posts a Slack alert to the cost channel.

It also sets the service's javabin:desired-count tag to 0. ecs-deploy reads the
desired count from that tag, so app pushes keep deploying with 0 tasks; the
count only comes back when a tf-apply writes compute.desired_count back to the
tag (a reviewed plan shows "javabin:desired-count" = "0" -> "N").

Does NOT destroy services — just scales to zero for easy recovery.
"""

import json
import logging
import os
from datetime import datetime, timezone

import boto3
from shared.constants import USD_TO_NOK
from shared.slack import get_webhook_url, post_to_slack

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ecs = boto3.client("ecs")
ssm = boto3.client("ssm")

CLUSTER = os.environ.get("ECS_CLUSTER", "javabin-platform")
COST_WEBHOOK_PARAM = os.environ["COST_WEBHOOK_PARAM"]
BUDGET_NAME_PREFIX = os.environ.get("BUDGET_NAME_PREFIX", "javabin-team-")
DESIRED_COUNT_TAG = "javabin:desired-count"


# ---------------------------------------------------------------------------
# ECS helpers
# ---------------------------------------------------------------------------
def list_services_for_team(cluster, team_name):
    """List ECS services in the cluster that are tagged with the given team."""
    matched = []
    paginator = ecs.get_paginator("list_services")

    for page in paginator.paginate(cluster=cluster):
        service_arns = page.get("serviceArns", [])
        if not service_arns:
            continue

        # DescribeServices accepts max 10 at a time
        for i in range(0, len(service_arns), 10):
            batch = service_arns[i : i + 10]
            desc = ecs.describe_services(cluster=cluster, services=batch, include=["TAGS"])
            for svc in desc.get("services", []):
                tags = {t["key"]: t["value"] for t in svc.get("tags", [])}
                if tags.get("team") == team_name:
                    matched.append(svc)

    return matched


def scale_service_to_zero(cluster, service_name):
    """Scale an ECS service to desired_count=0."""
    ecs.update_service(
        cluster=cluster,
        service=service_name,
        desiredCount=0,
    )
    logger.info("Scaled %s to 0 in cluster %s", service_name, cluster)


def pin_desired_count_to_zero(service_arn):
    """Set javabin:desired-count to 0 so ecs-deploy does not scale it back up."""
    ecs.tag_resource(
        resourceArn=service_arn,
        tags=[{"key": DESIRED_COUNT_TAG, "value": "0"}],
    )
    logger.info("Set %s=0 on %s", DESIRED_COUNT_TAG, service_arn)


# ---------------------------------------------------------------------------
# Slack notification
# ---------------------------------------------------------------------------
def build_alert_blocks(team_name, budget_name, services_scaled, budget_limit):
    """Build Slack Block Kit message for budget enforcement action."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    service_list = "\n".join(
        f"  - `{svc}`" for svc in services_scaled
    ) if services_scaled else "  _(no matching services found)_"

    nok_limit = float(budget_limit) * USD_TO_NOK if budget_limit else None
    limit_text = f"${budget_limit}" + (f" (~{nok_limit:.0f} NOK)" if nok_limit else "")

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":rotating_light: Budget Enforcement — Services Scaled to Zero",
                "emoji": True,
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Team *{team_name}* has exceeded *200%* of their monthly budget "
                    f"(`{budget_name}`, limit: {limit_text}).\n\n"
                    f"The following ECS services have been scaled to `desired_count=0`:"
                ),
            },
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": service_list,
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    ":arrow_right: *To restore services*, increase the team budget in "
                    "the registry, then run the app's pipeline: its tf-apply writes "
                    "`compute.desired_count` back to the `javabin:desired-count` tag "
                    "and ecs-deploy scales the service up. App pushes alone keep it at 0."
                ),
            },
        },
        {
            "type": "context",
            "elements": [
                {
                    "type": "mrkdwn",
                    "text": f"Enforced at {ts} | Cluster: `{CLUSTER}`",
                }
            ],
        },
    ]
    return blocks


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------
def handler(event, context):
    for record in event.get("Records", []):
        sns_message = record.get("Sns", {}).get("Message", "{}")

        try:
            message = json.loads(sns_message)
        except json.JSONDecodeError:
            logger.error("Failed to parse SNS message: %s", sns_message)
            continue

        budget_name = message.get("budgetName", "")
        if not budget_name.startswith(BUDGET_NAME_PREFIX):
            logger.info(
                "Ignoring budget notification for %s (not a team budget)", budget_name
            )
            continue

        team_name = budget_name[len(BUDGET_NAME_PREFIX) :]
        if not team_name:
            logger.error("Could not extract team name from budget: %s", budget_name)
            continue

        budget_limit = message.get("budgetLimit", {}).get("amount")

        logger.info(
            "Budget enforcement triggered for team %s (budget: %s)",
            team_name,
            budget_name,
        )

        # Find and scale down team's ECS services
        services = list_services_for_team(CLUSTER, team_name)
        services_scaled = []

        for svc in services:
            svc_name = svc["serviceName"]
            tags = {t["key"]: t["value"] for t in svc.get("tags", [])}
            if tags.get(DESIRED_COUNT_TAG, "0") != "0":
                # Also before the first deploy, while the service has 0 tasks
                try:
                    pin_desired_count_to_zero(svc["serviceArn"])
                except Exception as e:
                    logger.error("Failed to tag service %s: %s", svc_name, e)
            current_count = svc.get("desiredCount", 0)
            if current_count == 0:
                logger.info(
                    "Service %s already at desired_count=0, skipping", svc_name
                )
                continue
            try:
                scale_service_to_zero(CLUSTER, svc_name)
                services_scaled.append(svc_name)
            except Exception as e:
                logger.error("Failed to scale service %s: %s", svc_name, e)

        # Post Slack notification regardless of whether services were found
        blocks = build_alert_blocks(
            team_name, budget_name, services_scaled, budget_limit
        )
        fallback = (
            f"Budget enforcement: team {team_name} exceeded 200% — "
            f"{len(services_scaled)} service(s) scaled to zero"
        )

        try:
            webhook_url = get_webhook_url(ssm, COST_WEBHOOK_PARAM)
            post_to_slack(webhook_url, {"blocks": blocks, "text": fallback})
        except Exception as e:
            logger.error("Failed to send Slack notification: %s", e)

    return {"statusCode": 200, "body": "OK"}
