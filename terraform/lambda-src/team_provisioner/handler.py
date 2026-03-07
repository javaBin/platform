"""Team provisioner — syncs teams across Google Workspace, GitHub, and AWS Budgets.

Trigger: repository_dispatch from javaBin/registry (via function URL or SNS).
Event payload contains team YAML definitions.

Integrations:
- Google Admin SDK: Create/sync Google Workspace groups (domain-wide delegation SA)
- GitHub API: Create/sync GitHub teams (GitHub App installation token)
- AWS Budgets: Create team-level budget scoped to `team` tag
- Cognito: TODO — pools not deployed yet
- IAM Identity Center: TODO — not configured yet
"""

import base64
import json
import logging
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

import boto3
from shared.constants import USD_TO_NOK
from shared.slack import get_webhook_url, post_to_slack

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")
budgets_client = boto3.client("budgets")

GOOGLE_SA_PARAM = os.environ.get(
    "GOOGLE_SA_PARAM", "/javabin/platform/google-admin-sa"
)
GOOGLE_ADMIN_EMAIL_PARAM = os.environ.get(
    "GOOGLE_ADMIN_EMAIL_PARAM", "/javabin/platform/google-admin-email"
)
GITHUB_APP_ID_PARAM = os.environ.get(
    "GITHUB_APP_ID_PARAM", "/javabin/platform/github-app-id"
)
GITHUB_APP_KEY_PARAM = os.environ.get(
    "GITHUB_APP_KEY_PARAM", "/javabin/platform/github-app-key"
)
INFRA_WEBHOOK_PARAM = os.environ.get(
    "INFRA_WEBHOOK_PARAM", "/javabin/slack/platform-resource-alerts-webhook"
)
ACCOUNT_ID = os.environ.get("ACCOUNT_ID", "")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "javaBin")
ALERTS_TOPIC_ARN = os.environ.get("ALERTS_TOPIC_ARN", "")

# Cache credentials across invocations within the same Lambda container
_credential_cache = {}


def _get_ssm_param(param_name):
    """Fetch and cache an SSM parameter value."""
    if param_name not in _credential_cache:
        resp = ssm.get_parameter(Name=param_name, WithDecryption=True)
        _credential_cache[param_name] = resp["Parameter"]["Value"]
    return _credential_cache[param_name]


# ---------------------------------------------------------------------------
# JWT / RS256 helpers (stdlib only — uses openssl CLI for RSA signing)
# ---------------------------------------------------------------------------

def _b64url(data):
    """Base64url-encode without padding."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign_rs256(message_bytes, private_key_pem):
    """Sign with RS256 using the openssl binary (available in Lambda runtime)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".pem", delete=True
    ) as key_file:
        key_file.write(private_key_pem)
        key_file.flush()
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_file.name],
            input=message_bytes,
            capture_output=True,
        )
    if result.returncode != 0:
        raise RuntimeError(f"openssl signing failed: {result.stderr.decode()}")
    return result.stdout


def _create_jwt(payload, private_key_pem):
    """Create a signed RS256 JWT from a dict payload."""
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}))
    body = _b64url(json.dumps(payload))
    signing_input = f"{header}.{body}".encode("ascii")
    signature = _b64url(_sign_rs256(signing_input, private_key_pem))
    return f"{header}.{body}.{signature}"


# ---------------------------------------------------------------------------
# Google Admin SDK — domain-wide delegation
# ---------------------------------------------------------------------------

GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/admin.directory.group "
    "https://www.googleapis.com/auth/admin.directory.group.member"
)


def _get_google_access_token():
    """Exchange a domain-wide delegation JWT for a Google OAuth2 access token.

    Token is cached for ~58 minutes (Google tokens last 1 hour).
    """
    cached = _credential_cache.get("_google_token")
    if cached and cached["exp"] > time.time() + 120:
        return cached["token"]

    sa_json = json.loads(_get_ssm_param(GOOGLE_SA_PARAM))
    admin_email = _get_ssm_param(GOOGLE_ADMIN_EMAIL_PARAM)

    now = int(time.time())
    signed_jwt = _create_jwt(
        {
            "iss": sa_json["client_email"],
            "sub": admin_email,
            "scope": GOOGLE_SCOPES,
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        },
        sa_json["private_key"],
    )

    data = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": signed_jwt,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:
        token = json.loads(resp.read())["access_token"]

    _credential_cache["_google_token"] = {
        "token": token,
        "exp": time.time() + 3500,
    }
    return token


def _google_api(method, path, access_token, body=None):
    """Call the Google Admin Directory API. Returns parsed JSON or None."""
    url = f"https://admin.googleapis.com/admin/directory/v1{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 409:
            return {"already_exists": True}
        if e.code == 404:
            return None
        body_text = e.read().decode("utf-8", errors="replace")
        logger.error("Google API %s %s → %d: %s", method, path, e.code, body_text)
        raise


def sync_google_group(team):
    """Create or update a Google Workspace group and sync its membership."""
    group_email = team.get("google_group")
    if not group_email:
        logger.info("No google_group for team %s — skipping", team["name"])
        return {"skipped": True}

    access_token = _get_google_access_token()
    group_key = urllib.parse.quote(group_email, safe="")

    group_body = {
        "email": group_email,
        "name": team.get("display_name", team["name"]),
        "description": team.get("description", f"Javabin team: {team['name']}"),
    }

    existing = _google_api("GET", f"/groups/{group_key}", access_token)
    if existing and not existing.get("already_exists"):
        _google_api("PUT", f"/groups/{group_key}", access_token, group_body)
        logger.info("Updated Google group %s", group_email)
    else:
        result = _google_api("POST", "/groups", access_token, group_body)
        if result and result.get("already_exists"):
            logger.info("Google group %s already exists", group_email)
        else:
            logger.info("Created Google group %s", group_email)

    # Current members (paginated — Google returns max 200 per page)
    current_members = {}
    page_token = None
    while True:
        path = f"/groups/{group_key}/members?maxResults=200"
        if page_token:
            path += f"&pageToken={urllib.parse.quote(page_token, safe='')}"
        resp = _google_api("GET", path, access_token)
        if resp and "members" in resp:
            for m in resp["members"]:
                current_members[m["email"].lower()] = m.get("role", "MEMBER")
        if resp and resp.get("nextPageToken"):
            page_token = resp["nextPageToken"]
        else:
            break

    # Desired members
    desired_emails = set()
    for member in team.get("members", []):
        email = member.get("email", "").lower()
        if not email:
            continue
        desired_emails.add(email)
        role = "OWNER" if member.get("role") == "admin" else "MEMBER"
        encoded_email = urllib.parse.quote(email, safe="")

        if email in current_members:
            if current_members[email] != role:
                _google_api(
                    "PUT",
                    f"/groups/{group_key}/members/{encoded_email}",
                    access_token,
                    {"role": role},
                )
                logger.info("Updated %s to %s in %s", email, role, group_email)
        else:
            _google_api(
                "POST",
                f"/groups/{group_key}/members",
                access_token,
                {"email": email, "role": role},
            )
            logger.info("Added %s as %s to %s", email, role, group_email)

    # Remove members no longer in the team definition
    for email in current_members:
        if email not in desired_emails:
            encoded_email = urllib.parse.quote(email, safe="")
            _google_api(
                "DELETE",
                f"/groups/{group_key}/members/{encoded_email}",
                access_token,
            )
            logger.info("Removed %s from %s", email, group_email)

    return {
        "synced": True,
        "group": group_email,
        "member_count": len(desired_emails),
    }


# ---------------------------------------------------------------------------
# GitHub API — App authentication
# ---------------------------------------------------------------------------

_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _get_github_installation_token():
    """Create a GitHub App installation token for the javaBin org.

    Token is cached for ~9 minutes (GitHub installation tokens last 10 min).
    """
    cached = _credential_cache.get("_github_token")
    if cached and cached["exp"] > time.time() + 60:
        return cached["token"]

    app_id = _get_ssm_param(GITHUB_APP_ID_PARAM)
    app_key = _get_ssm_param(GITHUB_APP_KEY_PARAM)

    now = int(time.time())
    app_jwt = _create_jwt(
        {"iat": now - 60, "exp": now + 600, "iss": app_id}, app_key
    )

    # Resolve the org installation ID
    req = urllib.request.Request(
        f"https://api.github.com/orgs/{GITHUB_ORG}/installation",
        headers={**_GITHUB_HEADERS, "Authorization": f"Bearer {app_jwt}"},
    )
    with urllib.request.urlopen(req) as resp:
        installation_id = json.loads(resp.read())["id"]

    # Exchange for an installation access token
    req = urllib.request.Request(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        data=b"{}",
        method="POST",
        headers={**_GITHUB_HEADERS, "Authorization": f"Bearer {app_jwt}"},
    )
    with urllib.request.urlopen(req) as resp:
        token = json.loads(resp.read())["token"]

    _credential_cache["_github_token"] = {
        "token": token,
        "exp": time.time() + 540,
    }
    return token


def _github_api(method, path, token, body=None):
    """Call the GitHub REST API. Returns parsed JSON, empty dict, or None (404)."""
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    for k, v in _GITHUB_HEADERS.items():
        req.add_header(k, v)
    if data:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 422:
            return {"already_exists": True}
        if e.code == 404:
            return None
        body_text = e.read().decode("utf-8", errors="replace")
        logger.error("GitHub API %s %s → %d: %s", method, path, e.code, body_text)
        raise


def sync_github_team(team):
    """Create or update a GitHub team and sync its membership."""
    team_name = team["name"]
    token = _get_github_installation_token()
    team_slug = team_name.lower().replace(" ", "-")

    team_body = {
        "name": team_name,
        "description": team.get("description", f"Javabin team: {team_name}"),
        "privacy": "closed",
    }

    existing = _github_api(
        "GET", f"/orgs/{GITHUB_ORG}/teams/{team_slug}", token
    )
    if existing and not existing.get("already_exists"):
        _github_api(
            "PATCH",
            f"/orgs/{GITHUB_ORG}/teams/{team_slug}",
            token,
            team_body,
        )
        logger.info("Updated GitHub team %s", team_slug)
    else:
        result = _github_api(
            "POST", f"/orgs/{GITHUB_ORG}/teams", token, team_body
        )
        if result and result.get("already_exists"):
            logger.info("GitHub team %s already exists", team_slug)
        else:
            logger.info("Created GitHub team %s", team_slug)

    # Current team members (paginated — GitHub returns max 100 per page)
    current_members = set()
    page = 1
    while True:
        members_resp = _github_api(
            "GET",
            f"/orgs/{GITHUB_ORG}/teams/{team_slug}/members?per_page=100&page={page}",
            token,
        )
        if not isinstance(members_resp, list) or not members_resp:
            break
        current_members.update(m["login"].lower() for m in members_resp)
        if len(members_resp) < 100:
            break
        page += 1

    # Desired members
    desired_users = set()
    for member in team.get("members", []):
        github_user = member.get("github", "").lower()
        if not github_user:
            continue
        desired_users.add(github_user)
        role = "maintainer" if member.get("role") == "admin" else "member"
        _github_api(
            "PUT",
            f"/orgs/{GITHUB_ORG}/teams/{team_slug}/memberships/{github_user}",
            token,
            {"role": role},
        )
        logger.info("Set %s as %s in team %s", github_user, role, team_slug)

    # Remove members no longer in the team definition
    for login in current_members - desired_users:
        _github_api(
            "DELETE",
            f"/orgs/{GITHUB_ORG}/teams/{team_slug}/memberships/{login}",
            token,
        )
        logger.info("Removed %s from GitHub team %s", login, team_slug)

    return {
        "synced": True,
        "team": team_slug,
        "member_count": len(desired_users),
    }


# ---------------------------------------------------------------------------
# AWS Budgets — team-level budget scoped to the `team` cost allocation tag
# ---------------------------------------------------------------------------

def sync_budget(team):
    """Create or update an AWS Budget for this team."""
    budget_nok = team.get("budget_nok")
    if not budget_nok:
        logger.info("No budget_nok for team %s — skipping", team["name"])
        return {"skipped": True}

    budget_usd = round(budget_nok / USD_TO_NOK, 2)
    budget_name = f"javabin-team-{team['name']}"

    budget_spec = {
        "BudgetName": budget_name,
        "BudgetLimit": {"Amount": str(budget_usd), "Unit": "USD"},
        "BudgetType": "COST",
        "TimeUnit": "MONTHLY",
        "CostFilters": {"TagKeyValue": [f"user:team${team['name']}"]},
    }

    try:
        budgets_client.describe_budget(
            AccountId=ACCOUNT_ID, BudgetName=budget_name
        )
        budgets_client.update_budget(
            AccountId=ACCOUNT_ID, NewBudget=budget_spec
        )
        logger.info("Updated budget %s: $%.2f/mo", budget_name, budget_usd)
    except budgets_client.exceptions.NotFoundException:
        notification = {
            "Notification": {
                "NotificationType": "ACTUAL",
                "ComparisonOperator": "GREATER_THAN",
                "Threshold": 80.0,
                "ThresholdType": "PERCENTAGE",
            },
            "Subscribers": [
                {"SubscriptionType": "SNS", "Address": ALERTS_TOPIC_ARN}
            ],
        }
        budgets_client.create_budget(
            AccountId=ACCOUNT_ID,
            Budget=budget_spec,
            NotificationsWithSubscribers=[notification],
        )
        logger.info("Created budget %s: $%.2f/mo", budget_name, budget_usd)

    return {"synced": True, "budget": budget_name, "usd": budget_usd}


# ---------------------------------------------------------------------------
# Cognito + Identity Center — stubs with clear extension points
# ---------------------------------------------------------------------------

def sync_cognito_group(team):
    """Sync team to Cognito user pool groups.

    TODO: Implement when Cognito user pools are deployed.
    Needs: internal_pool_id, external_pool_id from identity module outputs.
    Steps:
      1. Create group in internal pool (for java.no members)
      2. Create group in external pool (if team has external members)
      3. Assign users to groups via cognito-idp:AdminAddUserToGroup
    """
    logger.info(
        "Cognito sync skipped — pools not deployed (team: %s)", team["name"]
    )
    return {"skipped": True, "reason": "cognito_pools_not_deployed"}


def sync_identity_center_group(team):
    """Sync team to IAM Identity Center groups.

    TODO: Implement when Identity Center is configured.
    Needs: identity_store_id from identity module outputs.
    Steps:
      1. Create group via identitystore:CreateGroup
      2. Resolve user IDs via identitystore:ListUsers
      3. Assign via identitystore:CreateGroupMembership
    """
    logger.info(
        "Identity Center sync skipped — not configured (team: %s)",
        team["name"],
    )
    return {"skipped": True, "reason": "identity_center_not_configured"}


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------

def parse_team_payload(event):
    """Extract team definitions from the Lambda event.

    Supports three invocation patterns:
    1. Direct: {"teams": [...]}
    2. SNS-wrapped: {"Records": [{"Sns": {"Message": '{"teams": [...]}'}}]}
    3. Function URL: {"body": '{"teams": [...]}'}
    """
    if "teams" in event:
        return event["teams"]

    if "Records" in event:
        for record in event["Records"]:
            if "Sns" in record:
                message = json.loads(record["Sns"]["Message"])
                if "teams" in message:
                    return message["teams"]

    if "body" in event:
        body = (
            json.loads(event["body"])
            if isinstance(event["body"], str)
            else event["body"]
        )
        if "teams" in body:
            return body["teams"]

    logger.warning(
        "No team data found in event: %s",
        json.dumps(event, default=str)[:500],
    )
    return []


# ---------------------------------------------------------------------------
# Slack notification
# ---------------------------------------------------------------------------

def _result_line(provider, outcome):
    """Format a single provider sync result for Slack."""
    if not isinstance(outcome, dict):
        return f":question: {provider}: {outcome}"
    if outcome.get("skipped"):
        reason = outcome.get("reason", "skipped")
        return f":white_circle: {provider}: {reason}"
    if outcome.get("error"):
        return f":red_circle: {provider}: {outcome['error']}"
    return f":white_check_mark: {provider}: synced"


def notify_slack(results):
    """Post provisioning summary to Slack."""
    webhook_url = get_webhook_url(ssm, INFRA_WEBHOOK_PARAM)

    blocks = [
        {
            "type": "header",
            "text": {
                "type": "plain_text",
                "text": ":busts_in_silhouette: Team Provisioning Complete",
                "emoji": True,
            },
        }
    ]

    for team_name, result in results.items():
        lines = [_result_line(p, o) for p, o in result.items()]
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{team_name}*\n" + "\n".join(lines),
                },
            }
        )

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    blocks.extend(
        [
            {"type": "divider"},
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Team provisioner | {ts}"}
                ],
            },
        ]
    )

    post_to_slack(
        webhook_url,
        {
            "blocks": blocks,
            "text": f"Team provisioning: {', '.join(results.keys())}",
        },
    )


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def handler(event, context):
    logger.info("Team provisioner invoked")
    logger.info("Event: %s", json.dumps(event, default=str)[:1000])

    teams = parse_team_payload(event)
    if not teams:
        logger.info("No team data in event — nothing to provision")
        return {"statusCode": 200, "body": "No teams to provision"}

    results = {}

    for team in teams:
        team_name = team.get("name")
        if not team_name:
            logger.warning(
                "Team entry missing 'name' — skipping: %s",
                json.dumps(team)[:200],
            )
            continue

        logger.info("Provisioning team: %s", team_name)
        tr = {}

        for label, fn in [
            ("google", sync_google_group),
            ("github", sync_github_team),
            ("budget", sync_budget),
            ("cognito", sync_cognito_group),
            ("identity_center", sync_identity_center_group),
        ]:
            try:
                tr[label] = fn(team)
            except Exception as e:
                logger.error("%s sync failed for %s: %s", label, team_name, e)
                tr[label] = {"error": str(e)[:200]}

        results[team_name] = tr

    try:
        notify_slack(results)
    except Exception as e:
        logger.error("Slack notification failed: %s", e)

    return {
        "statusCode": 200,
        "body": json.dumps(
            {"provisioned": list(results.keys()), "results": results},
            default=str,
        ),
    }
