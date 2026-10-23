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
"""

import logging
import os
import time

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

TABLE = os.environ.get("MEMBERSHIPS_TABLE", "javabin-auth-memberships")
CACHE_SECONDS = int(os.environ.get("CACHE_SECONDS", "60"))

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


def allowed_groups(client_id):
    item = dynamodb.get_item(TableName=TABLE, Key={"pk": {"S": f"client#{client_id}"}}).get("Item")
    return set(_strings(item.get("allowed_groups"))) if item else set()


def handler(event, context):
    request = event.get("request", {})
    email = (request.get("userAttributes", {}).get("email") or "").lower()
    client_id = event.get("callerContext", {}).get("clientId", "")
    native = (request.get("groupConfiguration") or {}).get("groupsToOverride") or []

    registry = sorted(g for g, members in group_members().items() if email and email in members)
    groups = sorted(set(native) | set(registry))

    allowed = allowed_groups(client_id) if client_id else set()
    if allowed and not allowed & set(groups):
        logger.info("Denied %s for client %s: needs one of %s", email or "<no email>", client_id, sorted(allowed))
        raise AccessDenied("You are not in a group that has access to this app.")

    event["response"] = {
        "claimsAndScopeOverrideDetails": {
            "groupOverrideDetails": {"groupsToOverride": groups},
        }
    }
    return event
