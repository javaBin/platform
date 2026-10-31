"""Budget enforcer — suspends a team that exceeds 200% of its monthly budget.

Triggers:
  - SNS notification from AWS Budgets (200% threshold): suspend the team.
  - EventBridge schedule, {"action": "reconcile"}: keep every suspension in
    force, and lift the ones whose budget is back under 200% (new month or a
    raised budget_nok in the registry).
  - Manual invoke, {"action": "lift", "team": "<team>"}: lift early.

A suspension is an SSM marker at {SUSPENSION_PATH}/{team}. While it exists:
  - the team's ECS services stay at desired_count=0. The ecs-service module
    reads the marker and plans desired_count=0 too, so a terraform apply does
    not scale them back up;
  - the team's RDS instances are stopped. RDS starts a stopped instance again
    after 7 days, so reconcile stops it again.

Lifting deletes the marker, sets the services back to the desired count they
had before, and starts the RDS instances the enforcer stopped. Nothing is
ever deleted.
"""

import json
import logging
import os
import re
from datetime import datetime, timezone

import boto3
from shared.constants import USD_TO_NOK
from shared.slack import get_webhook_url, post_to_slack

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ecs = boto3.client("ecs")
rds = boto3.client("rds")
ssm = boto3.client("ssm")
budgets = boto3.client("budgets")

CLUSTER = os.environ.get("ECS_CLUSTER", "javabin-platform")
COST_WEBHOOK_PARAM = os.environ["COST_WEBHOOK_PARAM"]
BUDGET_NAME_PREFIX = os.environ.get("BUDGET_NAME_PREFIX", "javabin-team-")
SUSPENSION_PATH = os.environ.get("SUSPENSION_PATH", "/javabin/budget-suspensions").rstrip("/")
ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "")

# Suspension holds while actual spend is above this share of the budget limit.
ENFORCE_RATIO = 2.0

# AWS Budgets SNS notifications are plain text, e.g. "Budget Name: javabin-team-x".
_TEXT_FIELD = re.compile(r"^\s*(Budget Name|Budgeted Amount)\s*:\s*\$?\s*(\S+)\s*$", re.MULTILINE)


# ---------------------------------------------------------------------------
# Budget notification parsing
# ---------------------------------------------------------------------------
def parse_budget_message(text):
    """Return (budget_name, budget_limit) from an AWS Budgets SNS message.

    AWS Budgets sends plain text; JSON ({"budgetName", "budgetLimit"}) is
    accepted too for manual tests.
    """
    try:
        message = json.loads(text)
    except (json.JSONDecodeError, TypeError):
        message = None
    if isinstance(message, dict):
        return message.get("budgetName", ""), (message.get("budgetLimit") or {}).get("amount")

    fields = {k: v for k, v in _TEXT_FIELD.findall(text or "")}
    limit = fields.get("Budgeted Amount")
    return fields.get("Budget Name", ""), limit.replace(",", "") if limit else None


# ---------------------------------------------------------------------------
# Suspension marker (SSM)
# ---------------------------------------------------------------------------
def marker_name(team):
    return f"{SUSPENSION_PATH}/{team}"


def read_marker(team):
    try:
        resp = ssm.get_parameter(Name=marker_name(team))
    except ssm.exceptions.ParameterNotFound:
        return None
    try:
        return json.loads(resp["Parameter"]["Value"])
    except (json.JSONDecodeError, KeyError):
        return {}


def write_marker(team, state):
    ssm.put_parameter(
        Name=marker_name(team),
        Value=json.dumps(state, sort_keys=True),
        Type="String",
        Overwrite=True,
        Description=f"Budget suspension for team {team} (budget-enforcer)",
    )


def delete_marker(team):
    try:
        ssm.delete_parameter(Name=marker_name(team))
    except ssm.exceptions.ParameterNotFound:
        pass


def suspended_teams():
    teams = []
    for page in ssm.get_paginator("get_parameters_by_path").paginate(Path=SUSPENSION_PATH):
        for p in page.get("Parameters", []):
            teams.append(p["Name"].rsplit("/", 1)[-1])
    return teams


# ---------------------------------------------------------------------------
# ECS
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


def set_desired_count(cluster, service_name, count):
    ecs.update_service(cluster=cluster, service=service_name, desiredCount=count)
    logger.info("Set %s to desired_count=%d in cluster %s", service_name, count, cluster)


def scale_team_to_zero(team, previous):
    """Scale the team's running services to 0. Records each service's previous
    desired count in `previous` (first value wins). Returns the names scaled."""
    scaled = []
    for svc in list_services_for_team(CLUSTER, team):
        name, count = svc["serviceName"], svc.get("desiredCount", 0)
        if count == 0:
            continue
        try:
            set_desired_count(CLUSTER, name, 0)
        except Exception as e:
            logger.error("Failed to scale service %s: %s", name, e)
            continue
        previous.setdefault(name, count)
        scaled.append(name)
    return scaled


def restore_team_services(team, previous):
    """Set services the enforcer scaled back to their previous count, if still at 0."""
    restored = []
    for svc in list_services_for_team(CLUSTER, team):
        name = svc["serviceName"]
        count = previous.get(name)
        if not count or svc.get("desiredCount", 0) != 0:
            continue
        try:
            set_desired_count(CLUSTER, name, count)
            restored.append(name)
        except Exception as e:
            logger.error("Failed to restore service %s: %s", name, e)
    return restored


# ---------------------------------------------------------------------------
# RDS
# ---------------------------------------------------------------------------
def list_db_instances_for_team(team_name):
    matched = []
    for page in rds.get_paginator("describe_db_instances").paginate():
        for db in page.get("DBInstances", []):
            tags = {t["Key"]: t["Value"] for t in db.get("TagList", [])}
            if tags.get("team") == team_name:
                matched.append(db)
    return matched


def _stoppable(db):
    # Aurora members are stopped per cluster; replicas and their sources can't be stopped.
    return not (db.get("DBClusterIdentifier")
                or db.get("ReadReplicaSourceDBInstanceIdentifier")
                or db.get("ReadReplicaDBInstanceIdentifiers"))


def stop_team_databases(team, stopped):
    """Stop the team's available RDS instances. Adds their IDs to `stopped`."""
    newly = []
    for db in list_db_instances_for_team(team):
        ident = db["DBInstanceIdentifier"]
        if db.get("DBInstanceStatus") != "available":
            continue
        if not _stoppable(db):
            logger.info("RDS instance %s cannot be stopped (cluster or replica), skipping", ident)
            continue
        try:
            rds.stop_db_instance(DBInstanceIdentifier=ident)
        except Exception as e:
            logger.error("Failed to stop RDS instance %s: %s", ident, e)
            continue
        logger.info("Stopped RDS instance %s", ident)
        if ident not in stopped:
            stopped.append(ident)
        newly.append(ident)
    return newly


def start_team_databases(stopped):
    started = []
    for ident in stopped:
        try:
            rds.start_db_instance(DBInstanceIdentifier=ident)
            started.append(ident)
            logger.info("Started RDS instance %s", ident)
        except rds.exceptions.InvalidDBInstanceStateFault:
            logger.info("RDS instance %s is not stopped, leaving it", ident)
        except Exception as e:
            logger.error("Failed to start RDS instance %s: %s", ident, e)
    return started


# ---------------------------------------------------------------------------
# Budget state
# ---------------------------------------------------------------------------
def over_limit(team):
    """True while the team's actual spend this month is above 200% of its limit.

    Unknown (no budget, API error) counts as still over, so a suspension is
    never lifted by accident.
    """
    try:
        b = budgets.describe_budget(AccountId=ACCOUNT_ID, BudgetName=f"{BUDGET_NAME_PREFIX}{team}")["Budget"]
        limit = float(b["BudgetLimit"]["Amount"])
        actual = float(b["CalculatedSpend"]["ActualSpend"]["Amount"])
    except Exception as e:
        logger.error("Could not read budget for team %s: %s", team, e)
        return True
    return actual > limit * ENFORCE_RATIO


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
def suspend(team, budget_name, budget_limit):
    state = read_marker(team) or {}
    state.setdefault("suspended_at", datetime.now(timezone.utc).isoformat(timespec="seconds"))
    state["budget"] = budget_name
    services = state.setdefault("services", {})
    stopped = state.setdefault("rds", [])
    # Marker first: from here on a terraform apply plans desired_count=0.
    write_marker(team, state)

    scaled = scale_team_to_zero(team, services)
    dbs = stop_team_databases(team, stopped)
    write_marker(team, state)

    notify(build_suspend_blocks(team, budget_name, scaled, dbs, budget_limit),
           f"Budget enforcement: team {team} exceeded 200% — {len(scaled)} service(s) "
           f"scaled to zero, {len(dbs)} database(s) stopped")
    return {"team": team, "action": "suspended", "services": scaled, "rds": dbs}


def lift(team, reason):
    state = read_marker(team)
    if state is None:
        return {"team": team, "action": "not_suspended"}
    # Marker first: the restored count matches what terraform now plans.
    delete_marker(team)
    restored = restore_team_services(team, state.get("services") or {})
    started = start_team_databases(state.get("rds") or [])
    notify(build_lift_blocks(team, reason, restored, started),
           f"Budget suspension lifted for team {team} ({reason})")
    return {"team": team, "action": "lifted", "services": restored, "rds": started}


def reapply(team):
    """Keep a suspension in force: re-zero services and re-stop databases."""
    state = read_marker(team) or {}
    services = state.setdefault("services", {})
    stopped = state.setdefault("rds", [])
    scaled = scale_team_to_zero(team, services)
    dbs = stop_team_databases(team, stopped)
    if scaled or dbs:
        write_marker(team, state)
        logger.info("Re-applied suspension for %s: services=%s rds=%s", team, scaled, dbs)
    return {"team": team, "action": "enforced", "services": scaled, "rds": dbs}


def reconcile():
    results = []
    for team in suspended_teams():
        try:
            if over_limit(team):
                results.append(reapply(team))
            else:
                results.append(lift(team, "spend is back under 200% of budget"))
        except Exception as e:
            logger.error("Reconcile failed for team %s: %s", team, e)
            results.append({"team": team, "action": "error", "error": str(e)[:200]})
    return results


# ---------------------------------------------------------------------------
# Slack notification
# ---------------------------------------------------------------------------
def notify(blocks, fallback):
    try:
        webhook_url = get_webhook_url(ssm, COST_WEBHOOK_PARAM)
        post_to_slack(webhook_url, {"blocks": blocks, "text": fallback})
    except Exception as e:
        logger.error("Failed to send Slack notification: %s", e)


def _bullets(items, empty):
    return "\n".join(f"  - `{i}`" for i in items) if items else f"  _{empty}_"


def _context():
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return {"type": "context", "elements": [{"type": "mrkdwn", "text": f"{ts} | Cluster: `{CLUSTER}`"}]}


def build_suspend_blocks(team_name, budget_name, services_scaled, dbs_stopped, budget_limit):
    """Build Slack Block Kit message for budget enforcement action."""
    try:
        nok_limit = float(budget_limit) * USD_TO_NOK if budget_limit else None
    except ValueError:
        nok_limit = None
    limit_text = f"${budget_limit}" + (f" (~{nok_limit:.0f} NOK)" if nok_limit else "")

    return [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":rotating_light: Budget Enforcement — Team Suspended",
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
                    "ECS services scaled to `desired_count=0`:\n"
                    + _bullets(services_scaled, "(none running)")
                    + "\n\nRDS instances stopped:\n"
                    + _bullets(dbs_stopped, "(none running)")
                ),
            },
        },
        {"type": "divider"},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f":lock: The suspension holds until it is lifted: deploys keep "
                    f"`desired_count=0` while `{marker_name(team_name)}` exists.\n"
                    ":arrow_right: *To lift it*, raise `budget_nok` in the registry (lifted within "
                    "6 hours), wait for the next month, or ask a platform admin to invoke "
                    "`javabin-budget-enforcer` with `{\"action\": \"lift\", \"team\": \""
                    f"{team_name}\"}}`. Services and databases come back on their own."
                ),
            },
        },
        _context(),
    ]


def build_lift_blocks(team_name, reason, restored, started):
    return [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": ":white_check_mark: Budget Suspension Lifted", "emoji": True},
        },
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": (
                    f"Team *{team_name}* is no longer suspended ({reason}).\n\n"
                    "ECS services restored:\n" + _bullets(restored, "(none)")
                    + "\n\nRDS instances started:\n" + _bullets(started, "(none)")
                    + "\n\nServices created during the suspension come up on the next deploy."
                ),
            },
        },
        _context(),
    ]


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------
def handler(event, context):
    action = event.get("action")
    if action == "reconcile":
        return {"statusCode": 200, "results": reconcile()}
    if action == "lift":
        team = event.get("team", "")
        if not team:
            return {"statusCode": 400, "body": "team is required"}
        return {"statusCode": 200, "results": [lift(team, "lifted manually")]}

    results = []
    for record in event.get("Records", []):
        sns_message = record.get("Sns", {}).get("Message", "")
        budget_name, budget_limit = parse_budget_message(sns_message)
        if not budget_name:
            logger.error("No budget name in SNS message: %s", sns_message[:500])
            continue
        if not budget_name.startswith(BUDGET_NAME_PREFIX):
            logger.info("Ignoring budget notification for %s (not a team budget)", budget_name)
            continue

        team_name = budget_name[len(BUDGET_NAME_PREFIX) :]
        if not team_name or "/" in team_name:
            logger.error("Could not extract team name from budget: %s", budget_name)
            continue

        logger.info("Budget enforcement triggered for team %s (budget: %s)", team_name, budget_name)
        results.append(suspend(team_name, budget_name, budget_limit))

    return {"statusCode": 200, "body": "OK", "results": results}
