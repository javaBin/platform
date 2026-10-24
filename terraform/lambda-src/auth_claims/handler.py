"""Auth claims — Cognito pre-token-generation (V2) trigger for the internal pool.

Federated users (GoogleSAML) sign in with only an email; the pool has no group
membership for them. Registry membership is written to the memberships table
by team-provisioner:

  {pk: "group#<name>", members: ["first.last@java.no", ...]}

At every sign-in and token refresh this trigger:
  1. looks up the user's email in those groups and sets cognito:groups in the
     ID and access token (kept alongside any native Cognito groups);
  2. reads {pk: "client#<client_id>", allowed_groups: [...]} (written by
     app-auth-sync from app.yaml auth.groups). When a client lists allowed
     groups and the user is in none of them, raising here makes Cognito
     refuse the sign-in, so the app never receives a token.

Clients without an entry, or with an empty list, accept any pool user.

When REQUIRED_EMAIL_DOMAIN is set (internal pool only, e.g. java.no), the
trigger first refuses every user whose email is not @<domain> (case-insensitive)
or whose address is not vouched for: the user must come from one of
FEDERATED_IDENTITY_PROVIDERS (the pool's sign-in IdPs, which only release
Workspace accounts, e.g. GoogleSAML) or have email_verified=true. This is the
server-side guard against self-registered or foreign-IdP users in the internal
pool; the IdP's hosted-domain hint is only a UI hint.

IdPs in EMAIL_VERIFIED_IDENTITY_PROVIDERS (default: Google, the OIDC IdP) are
not trusted on their own: their users also need email_verified=true, which
the pool maps from Google's own claim. SAML releases no such claim.
"""

import json
import logging
import os
import time

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

TABLE = os.environ.get("MEMBERSHIPS_TABLE", "javabin-auth-memberships")
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "60"))
REQUIRED_DOMAIN = os.environ.get("REQUIRED_EMAIL_DOMAIN", "").strip().lower().lstrip("@")
FEDERATED_IDPS = {p.strip() for p in os.environ.get("FEDERATED_IDENTITY_PROVIDERS", "").split(",")
                  if p.strip() and p.strip() != "COGNITO"}
# Federated IdPs whose users must still have email_verified=true
VERIFY_EMAIL_IDPS = {p.strip() for p in os.environ.get("EMAIL_VERIFIED_IDENTITY_PROVIDERS", "Google").split(",")
                     if p.strip()}

dynamodb = boto3.client("dynamodb")

_cache = {"groups": None, "loaded_at": 0}


class AccessDenied(Exception):
    """Raised to make Cognito refuse the token. Cognito shows the message."""


def _strings(attr):
    """Values of a DynamoDB L (of S) or SS attribute."""
    if not attr:
        return []
    if "SS" in attr:
        return attr["SS"]
    return [v["S"] for v in attr.get("L", []) if "S" in v]


def group_members():
    """{group: {lowercased emails}} — scanned and cached per container."""
    if _cache["groups"] is None or time.time() - _cache["loaded_at"] > CACHE_SECONDS:
        groups, kwargs = {}, {"TableName": TABLE}
        while True:
            page = dynamodb.scan(**kwargs)
            for item in page.get("Items", []):
                pk = item["pk"]["S"]
                if pk.startswith("group#"):
                    groups[pk[len("group#"):]] = {e.lower() for e in _strings(item.get("members"))}
            if "LastEvaluatedKey" not in page:
                break
            kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
        _cache.update(groups=groups, loaded_at=time.time())
    return _cache["groups"]


def _masked(email):
    """e***@java.no — enough to correlate in logs without storing the address."""
    if "@" not in email:
        return "<no email>"
    local, domain = email.split("@", 1)
    return f"{local[:1]}***@{domain}"


def allowed_groups(client_id):
    item = dynamodb.get_item(TableName=TABLE, Key={"pk": {"S": f"client#{client_id}"}}).get("Item")
    return set(_strings(item.get("allowed_groups"))) if item else set()


def _providers(event, attrs):
    """Names of the federated IdPs this user signed in through (empty for native users)."""
    names = set()
    try:
        identities = json.loads(attrs.get("identities") or "[]")
    except (TypeError, ValueError):
        identities = []
    if isinstance(identities, list):
        names |= {i.get("providerName") for i in identities if isinstance(i, dict) and i.get("providerName")}
    # Federated usernames are "<ProviderName>_<id>"; covers events without
    # identities. Not spoofable: the pool uses email as the sign-in alias, so
    # Cognito assigns native users a UUID username.
    user_name = event.get("userName") or ""
    names |= {p for p in FEDERATED_IDPS if user_name.startswith(p + "_")}
    return names


def domain_allowed(event, attrs, email):
    """True when the pool has no domain rule, or the user satisfies it."""
    if not REQUIRED_DOMAIN:
        return True
    if not email.endswith("@" + REQUIRED_DOMAIN):
        return False
    if _providers(event, attrs) & (FEDERATED_IDPS - VERIFY_EMAIL_IDPS):
        return True
    return str(attrs.get("email_verified", "")).lower() == "true"


def handler(event, context):
    request = event.get("request", {})
    attrs = request.get("userAttributes") or {}
    email = (attrs.get("email") or "").lower()

    if not domain_allowed(event, attrs, email):
        logger.info("Denied %s: not a verified @%s account", _masked(email), REQUIRED_DOMAIN)
        raise AccessDenied(f"Sign in with your @{REQUIRED_DOMAIN} account.")

    client_id = event.get("callerContext", {}).get("clientId", "")
    native = (request.get("groupConfiguration") or {}).get("groupsToOverride") or []

    registry = sorted(g for g, members in group_members().items() if email and email in members)
    groups = sorted(set(native) | set(registry))

    allowed = allowed_groups(client_id) if client_id else set()
    if allowed and not allowed & set(groups):
        logger.info("Denied %s for client %s: needs one of %s", _masked(email), client_id, sorted(allowed))
        raise AccessDenied("You are not in a group that has access to this app.")

    event["response"] = {
        "claimsAndScopeOverrideDetails": {
            "groupOverrideDetails": {"groupsToOverride": groups},
        }
    }
    return event
