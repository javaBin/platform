"""Team provisioner — syncs teams across Google, GitHub, AWS Budgets, Cognito, and Identity Center.

Trigger: Direct Lambda invocation from javaBin/registry CI (via javabin-ci-registry OIDC role).
Event payload contains team YAML definitions.

Integrations:
- Google Admin SDK: Create/sync Google Workspace groups (domain-wide delegation SA)
- GitHub API: Create/sync GitHub teams (GitHub App installation token)
- AWS Budgets: Create team-level budget scoped to `team` tag
- Cognito: Create/sync groups in internal user pool, assign members by email
- IAM Identity Center: Create/sync groups in identity store, assign members by email
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
cognito_client = boto3.client("cognito-idp")
identitystore_client = boto3.client("identitystore")

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
COGNITO_INTERNAL_POOL_ID = os.environ.get("COGNITO_INTERNAL_POOL_ID", "")
IDENTITY_STORE_ID = os.environ.get("IDENTITY_STORE_ID", "")
SSO_INSTANCE_ARN = os.environ.get("SSO_INSTANCE_ARN", "")
PROJECT = os.environ.get("PROJECT", "javabin")

sso_client = boto3.client("sso-admin")

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
    try:
        with urllib.request.urlopen(req) as resp:
            token = json.loads(resp.read())["access_token"]
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        logger.error("Google OAuth token exchange failed: %d %s", e.code, body_text)
        raise

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


def _normalize_member(member):
    """Normalize a member entry to a dict with email and github fields.

    Supports two formats:
    - Simple string: "alexander.amiri"
        → email: alexander.amiri@java.no (Google Workspace username)
        → github: not set (must use dict format for GitHub team sync)
    - Dict: {google: "alexander.amiri", github: "alexanderamiri"}
        → email: alexander.amiri@java.no (derived from google field)
        → github: alexanderamiri

    The 'google' field is the username part only — @java.no is always appended.
    Legacy 'email' field is also accepted for backwards compatibility.
    Members without a 'github' field are synced to Google Groups only.
    """
    if isinstance(member, str):
        username = member.strip()
        return {"email": f"{username}@java.no"}
    if isinstance(member, dict):
        result = dict(member)
        # Derive email from 'google' field if 'email' is not set
        if "email" not in result and "google" in result:
            result["email"] = f"{result['google']}@java.no"
        return result
    logger.warning("Unexpected member format: %s", member)
    return {}


def sync_google_group(team):
    """Create or update a Google Workspace group and sync its membership.

    Group email is derived as team-{name}@java.no. All members are MEMBER role.
    """
    team_name = team["name"]
    group_email = f"team-{team_name}@java.no"

    access_token = _get_google_access_token()
    group_key = urllib.parse.quote(group_email, safe="")

    group_body = {
        "email": group_email,
        "name": team_name,
        "description": team["description"],
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
    current_emails = set()
    page_token = None
    while True:
        path = f"/groups/{group_key}/members?maxResults=200"
        if page_token:
            path += f"&pageToken={urllib.parse.quote(page_token, safe='')}"
        resp = _google_api("GET", path, access_token)
        if resp and "members" in resp:
            for m in resp["members"]:
                current_emails.add(m["email"].lower())
        if resp and resp.get("nextPageToken"):
            page_token = resp["nextPageToken"]
        else:
            break

    # Desired members
    desired_emails = set()
    for member in team.get("members", []):
        m = _normalize_member(member)
        email = m.get("email", "").lower()
        if not email:
            continue
        desired_emails.add(email)

        if email not in current_emails:
            _google_api(
                "POST",
                f"/groups/{group_key}/members",
                access_token,
                {"email": email, "role": "MEMBER"},
            )
            logger.info("Added %s to %s", email, group_email)

    # Remove members no longer in the team definition
    for email in current_emails:
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
    """Create or update a GitHub team and sync its membership.

    All members are added as maintainers (no role distinction).
    """
    team_name = team["name"]
    token = _get_github_installation_token()
    team_slug = team_name.lower().replace(" ", "-")

    team_body = {
        "name": team_name,
        "description": team["description"],
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
        m = _normalize_member(member)
        github_user = m.get("github", "").lower()
        if not github_user:
            logger.warning(
                "No GitHub handle for member %s in team %s — skipping",
                member,
                team_name,
            )
            continue
        desired_users.add(github_user)
        _github_api(
            "PUT",
            f"/orgs/{GITHUB_ORG}/teams/{team_slug}/memberships/{github_user}",
            token,
            {"role": "maintainer"},
        )
        logger.info("Set %s as maintainer in team %s", github_user, team_slug)

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
    budget_nok = team.get("budget_nok", 500)

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
    """Create or update a Cognito group in the internal user pool and sync membership.

    Members are matched by email (username in Cognito). Members not yet in the
    pool are skipped (they'll be added on next sync after they sign up).
    """
    if not COGNITO_INTERNAL_POOL_ID:
        logger.info("Cognito sync skipped — no pool ID configured (team: %s)", team["name"])
        return {"skipped": True, "reason": "cognito_pool_not_configured"}

    team_name = team["name"]
    pool_id = COGNITO_INTERNAL_POOL_ID
    group_name = f"team-{team_name}"

    # Create or update group
    try:
        cognito_client.get_group(GroupName=group_name, UserPoolId=pool_id)
        cognito_client.update_group(
            GroupName=group_name,
            UserPoolId=pool_id,
            Description=team["description"],
        )
        logger.info("Updated Cognito group %s", group_name)
    except cognito_client.exceptions.ResourceNotFoundException:
        cognito_client.create_group(
            GroupName=group_name,
            UserPoolId=pool_id,
            Description=team["description"],
        )
        logger.info("Created Cognito group %s", group_name)

    # Current members in the group
    current_users = set()
    next_token = None
    while True:
        kwargs = {"GroupName": group_name, "UserPoolId": pool_id, "Limit": 60}
        if next_token:
            kwargs["NextToken"] = next_token
        resp = cognito_client.list_users_in_group(**kwargs)
        for user in resp.get("Users", []):
            for attr in user.get("Attributes", []):
                if attr["Name"] == "email":
                    current_users.add(attr["Value"].lower())
        next_token = resp.get("NextToken")
        if not next_token:
            break

    # Desired members — resolve email to Cognito username
    desired_emails = set()
    for member in team.get("members", []):
        m = _normalize_member(member)
        email = m.get("email", "").lower()
        if not email:
            continue
        desired_emails.add(email)

        if email not in current_users:
            # Look up user by email in the pool
            users_resp = cognito_client.list_users(
                UserPoolId=pool_id,
                Filter=f'email = "{email}"',
                Limit=1,
            )
            if users_resp.get("Users"):
                username = users_resp["Users"][0]["Username"]
                cognito_client.admin_add_user_to_group(
                    UserPoolId=pool_id,
                    Username=username,
                    GroupName=group_name,
                )
                logger.info("Added %s to Cognito group %s", email, group_name)
            else:
                logger.info("User %s not in Cognito pool — skipping", email)

    # Remove members no longer in the team
    for email in current_users - desired_emails:
        users_resp = cognito_client.list_users(
            UserPoolId=pool_id,
            Filter=f'email = "{email}"',
            Limit=1,
        )
        if users_resp.get("Users"):
            username = users_resp["Users"][0]["Username"]
            cognito_client.admin_remove_user_from_group(
                UserPoolId=pool_id,
                Username=username,
                GroupName=group_name,
            )
            logger.info("Removed %s from Cognito group %s", email, group_name)

    return {"synced": True, "group": group_name, "member_count": len(desired_emails)}


def sync_identity_center_group(team):
    """Create or update an Identity Center group and sync membership.

    Members are matched by email in the identity store. Members not yet
    provisioned in Identity Center are skipped.
    """
    if not IDENTITY_STORE_ID:
        logger.info(
            "Identity Center sync skipped — no store ID configured (team: %s)",
            team["name"],
        )
        return {"skipped": True, "reason": "identity_store_not_configured"}

    team_name = team["name"]
    group_name = f"team-{team_name}"
    store_id = IDENTITY_STORE_ID

    # Find or create group
    group_id = None
    groups_resp = identitystore_client.list_groups(
        IdentityStoreId=store_id,
        Filters=[{"AttributePath": "DisplayName", "AttributeValue": group_name}],
    )
    if groups_resp.get("Groups"):
        group_id = groups_resp["Groups"][0]["GroupId"]
        logger.info("Found Identity Center group %s (%s)", group_name, group_id)
    else:
        create_resp = identitystore_client.create_group(
            IdentityStoreId=store_id,
            DisplayName=group_name,
            Description=team["description"],
        )
        group_id = create_resp["GroupId"]
        logger.info("Created Identity Center group %s (%s)", group_name, group_id)

    # Current group memberships
    current_members = {}  # user_id -> membership_id
    next_token = None
    while True:
        kwargs = {"IdentityStoreId": store_id, "GroupId": group_id}
        if next_token:
            kwargs["NextToken"] = next_token
        resp = identitystore_client.list_group_memberships(**kwargs)
        for membership in resp.get("GroupMemberships", []):
            member_id = membership.get("MemberId", {}).get("UserId")
            if member_id:
                current_members[member_id] = membership["MembershipId"]
        next_token = resp.get("NextToken")
        if not next_token:
            break

    # Build reverse lookup: user_id -> email for current members
    current_emails = {}  # email -> user_id
    for user_id in current_members:
        try:
            user = identitystore_client.describe_user(
                IdentityStoreId=store_id, UserId=user_id
            )
            for email_obj in user.get("Emails", []):
                current_emails[email_obj["Value"].lower()] = user_id
        except Exception:
            logger.warning("Could not describe user %s", user_id)

    # Desired members
    desired_emails = set()
    for member in team.get("members", []):
        m = _normalize_member(member)
        email = m.get("email", "").lower()
        if not email:
            continue
        desired_emails.add(email)

        if email not in current_emails:
            # Look up user by email
            users_resp = identitystore_client.list_users(
                IdentityStoreId=store_id,
                Filters=[{"AttributePath": "UserName", "AttributeValue": email}],
            )
            if users_resp.get("Users"):
                user_id = users_resp["Users"][0]["UserId"]
                try:
                    identitystore_client.create_group_membership(
                        IdentityStoreId=store_id,
                        GroupId=group_id,
                        MemberId={"UserId": user_id},
                    )
                    logger.info("Added %s to Identity Center group %s", email, group_name)
                except identitystore_client.exceptions.ConflictException:
                    logger.info("User %s already in Identity Center group %s", email, group_name)
            else:
                logger.info("User %s not in Identity Center — skipping", email)

    # Remove members no longer in the team
    for email, user_id in current_emails.items():
        if email not in desired_emails:
            membership_id = current_members.get(user_id)
            if membership_id:
                identitystore_client.delete_group_membership(
                    IdentityStoreId=store_id,
                    MembershipId=membership_id,
                )
                logger.info("Removed %s from Identity Center group %s", email, group_name)

    return {"synced": True, "group": group_name, "member_count": len(desired_emails)}


# ---------------------------------------------------------------------------
# Group provisioning — create groups, sync members, assign permission sets
#
# Identity Center: SCIM syncs users from Google, but NOT group membership.
#   The provisioner reads Google group members and adds them to IC groups.
# Cognito: JIT creates users on first sign-in. Groups come via SAML claims.
#   The provisioner only creates group names, no member sync needed.
# ---------------------------------------------------------------------------

def _get_google_group_members(group_email):
    """Fetch all members of a Google Workspace group."""
    access_token = _get_google_access_token()
    group_key = urllib.parse.quote(group_email, safe="")

    members = []
    page_token = None
    while True:
        path = f"/groups/{group_key}/members?maxResults=200"
        if page_token:
            path += f"&pageToken={urllib.parse.quote(page_token, safe='')}"
        resp = _google_api("GET", path, access_token)
        if resp and "members" in resp:
            for m in resp["members"]:
                members.append(m["email"].lower())
        if resp and resp.get("nextPageToken"):
            page_token = resp["nextPageToken"]
        else:
            break

    return members


def sync_group_to_cognito(group):
    """Ensure a Cognito group exists. No member sync — JIT handles it at sign-in."""
    if not COGNITO_INTERNAL_POOL_ID:
        return {"skipped": True, "reason": "cognito_pool_not_configured"}
    if not group.get("cognito"):
        return {"skipped": True, "reason": "cognito_not_enabled"}

    group_name = group["name"]
    google_email = group["google"]
    pool_id = COGNITO_INTERNAL_POOL_ID

    try:
        cognito_client.get_group(GroupName=group_name, UserPoolId=pool_id)
        logger.info("Cognito group %s exists", group_name)
    except cognito_client.exceptions.ResourceNotFoundException:
        cognito_client.create_group(
            GroupName=group_name,
            UserPoolId=pool_id,
            Description=f"{group_name} (membership via Google sign-in, source: {google_email})",
        )
        logger.info("Created Cognito group %s", group_name)

    return {"synced": True, "group": group_name}


def sync_group_to_identity_center(group):
    """Create Identity Center group, sync members from Google, assign permission set.

    SCIM only provisions users — it does NOT sync Google group membership to
    Identity Center groups. This function bridges that gap by reading the
    Google group members and adding them to the Identity Center group.
    """
    if not IDENTITY_STORE_ID:
        return {"skipped": True, "reason": "identity_store_not_configured"}
    if not group.get("identity_center"):
        return {"skipped": True, "reason": "identity_center_not_enabled"}

    group_name = group["name"]
    google_email = group["google"]
    permission_set_name = group.get("permission_set")
    store_id = IDENTITY_STORE_ID

    # Find or create group
    group_id = None
    groups_resp = identitystore_client.list_groups(
        IdentityStoreId=store_id,
        Filters=[{"AttributePath": "DisplayName", "AttributeValue": group_name}],
    )
    if groups_resp.get("Groups"):
        group_id = groups_resp["Groups"][0]["GroupId"]
        logger.info("Identity Center group %s exists", group_name)
    else:
        create_resp = identitystore_client.create_group(
            IdentityStoreId=store_id,
            DisplayName=group_name,
            Description=f"{group_name} (synced from {google_email})",
        )
        group_id = create_resp["GroupId"]
        logger.info("Created Identity Center group %s", group_name)

    # Read members from Google group
    google_members = _get_google_group_members(google_email)

    # Current Identity Center group members
    current_members = {}  # user_id -> membership_id
    next_token = None
    while True:
        kwargs = {"IdentityStoreId": store_id, "GroupId": group_id}
        if next_token:
            kwargs["NextToken"] = next_token
        resp = identitystore_client.list_group_memberships(**kwargs)
        for membership in resp.get("GroupMemberships", []):
            member_id = membership.get("MemberId", {}).get("UserId")
            if member_id:
                current_members[member_id] = membership["MembershipId"]
        next_token = resp.get("NextToken")
        if not next_token:
            break

    # Reverse lookup: email -> user_id for current members
    current_emails = {}
    for user_id in current_members:
        try:
            user = identitystore_client.describe_user(
                IdentityStoreId=store_id, UserId=user_id
            )
            for email_obj in user.get("Emails", []):
                current_emails[email_obj["Value"].lower()] = user_id
        except Exception:
            pass

    # Add Google group members not yet in Identity Center group
    synced_count = 0
    for email in google_members:
        if email in current_emails:
            synced_count += 1
            continue
        # Look up user in Identity Center (must exist via SCIM)
        users_resp = identitystore_client.list_users(
            IdentityStoreId=store_id,
            Filters=[{"AttributePath": "UserName", "AttributeValue": email}],
        )
        if users_resp.get("Users"):
            user_id = users_resp["Users"][0]["UserId"]
            try:
                identitystore_client.create_group_membership(
                    IdentityStoreId=store_id,
                    GroupId=group_id,
                    MemberId={"UserId": user_id},
                )
                logger.info("Added %s to Identity Center group %s", email, group_name)
                synced_count += 1
            except identitystore_client.exceptions.ConflictException:
                synced_count += 1
        else:
            logger.info("User %s not in Identity Center (not SCIM-provisioned) — skipping", email)

    # Remove members no longer in the Google group
    desired = set(google_members)
    for email, user_id in current_emails.items():
        if email not in desired:
            membership_id = current_members.get(user_id)
            if membership_id:
                identitystore_client.delete_group_membership(
                    IdentityStoreId=store_id, MembershipId=membership_id,
                )
                logger.info("Removed %s from Identity Center group %s", email, group_name)

    # Assign permission set (if specified)
    result = {"synced": True, "group": group_name, "member_count": synced_count}
    if permission_set_name and SSO_INSTANCE_ARN:
        result["permission_set"] = _assign_permission_set(group_id, permission_set_name)

    return result


def _resolve_permission_set_arn(name):
    """Look up a permission set ARN by name (e.g. 'admin' -> javabin-admin ARN).

    Results are cached across invocations.
    """
    cache_key = f"_ps_{name}"
    if cache_key in _credential_cache:
        return _credential_cache[cache_key]

    full_name = f"{PROJECT}-{name}"
    paginator = sso_client.get_paginator("list_permission_sets")
    for page in paginator.paginate(InstanceArn=SSO_INSTANCE_ARN):
        for ps_arn in page.get("PermissionSets", []):
            resp = sso_client.describe_permission_set(
                InstanceArn=SSO_INSTANCE_ARN, PermissionSetArn=ps_arn,
            )
            if resp["PermissionSet"]["Name"] == full_name:
                _credential_cache[cache_key] = ps_arn
                return ps_arn

    logger.warning("Permission set %s not found", full_name)
    return None


def _assign_permission_set(group_id, permission_set_name):
    """Assign a permission set to an Identity Center group for the AWS account."""
    ps_arn = _resolve_permission_set_arn(permission_set_name)
    if not ps_arn:
        return {"skipped": True, "reason": f"permission set '{permission_set_name}' not found"}

    try:
        sso_client.create_account_assignment(
            InstanceArn=SSO_INSTANCE_ARN,
            TargetId=ACCOUNT_ID,
            TargetType="AWS_ACCOUNT",
            PermissionSetArn=ps_arn,
            PrincipalType="GROUP",
            PrincipalId=group_id,
        )
        logger.info("Assigned %s to group %s", permission_set_name, group_id)
        return {"assigned": True, "permission_set": permission_set_name}
    except sso_client.exceptions.ConflictException:
        logger.info("Permission set %s already assigned to group %s", permission_set_name, group_id)
        return {"assigned": True, "already_existed": True}
    except Exception as e:
        logger.error("Failed to assign permission set: %s", e)
        return {"error": str(e)[:200]}


def handle_sync_groups(event):
    """Handle the sync_groups action — process groups.yaml entries."""
    groups = event.get("groups", [])
    if not groups:
        return {"statusCode": 200, "body": "No groups to sync"}

    results = {}
    for group in groups:
        name = group.get("name")
        if not name or not group.get("google"):
            continue

        logger.info("Syncing group: %s (%s)", name, group["google"])
        gr = {}

        try:
            gr["cognito"] = sync_group_to_cognito(group)
        except Exception as e:
            logger.error("Cognito sync failed for %s: %s", name, e)
            gr["cognito"] = {"error": str(e)[:200]}

        try:
            gr["identity_center"] = sync_group_to_identity_center(group)
        except Exception as e:
            logger.error("Identity Center sync failed for %s: %s", name, e)
            gr["identity_center"] = {"error": str(e)[:200]}

        results[name] = gr

    try:
        notify_slack(results)
    except Exception as e:
        logger.error("Slack notification failed: %s", e)

    return {
        "statusCode": 200,
        "body": json.dumps({"synced": list(results.keys()), "results": results}, default=str),
    }


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------

def _extract_payload(event):
    """Unwrap the event payload from SNS, Function URL, or direct invocation."""
    if "Records" in event:
        for record in event["Records"]:
            if "Sns" in record:
                return json.loads(record["Sns"]["Message"])

    if "body" in event:
        body = event["body"]
        return json.loads(body) if isinstance(body, str) else body

    return event


def parse_team_payload(event):
    """Extract team definitions from the Lambda event.

    Supports three invocation patterns:
    1. Direct: {"teams": [...]}
    2. SNS-wrapped: {"Records": [{"Sns": {"Message": '{"teams": [...]}'}}]}
    3. Function URL: {"body": '{"teams": [...]}'}
    """
    payload = _extract_payload(event)
    if "teams" in payload:
        return payload["teams"]

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

    # Dispatch by action — sync_groups uses a different flow
    payload = _extract_payload(event)
    if payload.get("action") == "sync_groups":
        return handle_sync_groups(payload)

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

        if not team.get("description"):
            logger.warning(
                "Team %s missing required 'description' — skipping",
                team_name,
            )
            results[team_name] = {"error": "missing required 'description' field"}
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
