"""App auth sync — turns app.yaml `auth:` requests into Cognito app clients.

App Terraform (cognito-app-client module) writes the desired client config to
/{project}/apps/{team}/{app}/auth. Only team {team} can write under that path
(developer boundary, DenyBadName), so the path identifies the owner and no
caller-supplied identity is trusted.

For each request this Lambda keeps one client named "{team}-{app}" in the
requested pool, removes it from the other pool, and writes the credentials to

  /{project}/apps/{team}/{app}/cognito-client-id       (String)
  /{project}/apps/{team}/{app}/cognito-client-secret   (SecureString)

where the app's ECS task picks them up via its team's execution role.
Public clients (browser apps using PKCE) get no secret and no secret parameter.

The request's allowed groups are written to the auth memberships table as
{pk: "client#<client_id>", allowed_groups: [...]}; the auth-claims trigger
refuses tokens for users in none of them.

Triggers:
  - EventBridge "Parameter Store Change" for */auth under /{project}/apps/
    Create/Update -> sync; Delete -> remove client + credentials
  - Direct invoke {"action": "reconcile"} -> sync every request, remove
    clients whose request is gone
"""

import json
import logging
import os
import re

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

PROJECT = os.environ.get("PROJECT", "javabin")
POOL_NAMES = {
    "internal": os.environ.get("INTERNAL_POOL_NAME", f"{PROJECT}-internal"),
    "external": os.environ.get("EXTERNAL_POOL_NAME", f"{PROJECT}-external"),
}
MEMBERSHIPS_TABLE = os.environ.get("MEMBERSHIPS_TABLE", "")
DEFAULT_SCOPES = ["openid", "email", "profile"]
CLIENT_TYPES = ("confidential", "public")
# Cognito allows plain http only for localhost, for local development
LOCAL_URL = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?(/.*)?$")
ALLOWED_SCOPES = {"openid", "email", "profile", "phone", "aws.cognito.signin.user.admin"}

APPS_PREFIX = f"/{PROJECT}/apps/"
REQUEST_RE = re.compile(rf"^/{re.escape(PROJECT)}/apps/(?P<team>[a-z]{{1,12}})/(?P<app>[a-z][a-z0-9-]{{0,19}})/auth$")

ssm = boto3.client("ssm")
cognito = boto3.client("cognito-idp")
dynamodb = boto3.client("dynamodb")


class InvalidRequest(ValueError):
    pass


# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------

def parse_name(name):
    """Return (team, app) for a request parameter name, or None."""
    m = REQUEST_RE.match(name or "")
    return (m["team"], m["app"]) if m else None


def parse_request(raw):
    """Validate the JSON written by the cognito-app-client module."""
    try:
        req = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise InvalidRequest(f"not JSON: {e}")
    if not isinstance(req, dict):
        raise InvalidRequest("expected a JSON object")

    pool = req.get("pool")
    if pool not in POOL_NAMES:
        raise InvalidRequest(f"pool must be one of {sorted(POOL_NAMES)}, got {pool!r}")

    def urls(key, required):
        value = req.get(key) or []
        if not isinstance(value, list) or not all(
            isinstance(u, str) and (u.startswith("https://") or LOCAL_URL.match(u)) for u in value
        ):
            raise InvalidRequest(f"{key} must be a list of https:// (or http://localhost) URLs")
        if required and not value:
            raise InvalidRequest(f"{key} must not be empty")
        return value

    scopes = req.get("scopes") or DEFAULT_SCOPES
    if not isinstance(scopes, list) or not set(scopes) <= ALLOWED_SCOPES:
        raise InvalidRequest(f"scopes must be a subset of {sorted(ALLOWED_SCOPES)}")

    client_type = req.get("client_type") or "confidential"
    if client_type not in CLIENT_TYPES:
        raise InvalidRequest(f"client_type must be one of {list(CLIENT_TYPES)}, got {client_type!r}")

    groups = req.get("allowed_groups") or []
    if not isinstance(groups, list) or not all(isinstance(g, str) and g for g in groups):
        raise InvalidRequest("allowed_groups must be a list of group names")

    return {
        "pool": pool,
        "client_type": client_type,
        "callback_urls": urls("callback_urls", required=True),
        "logout_urls": urls("logout_urls", required=False),
        "scopes": scopes,
        "allowed_groups": sorted(set(groups)),
    }


# ---------------------------------------------------------------------------
# Cognito helpers
# ---------------------------------------------------------------------------

_pool_cache = {}


def pool_id(pool):
    if pool not in _pool_cache:
        name = POOL_NAMES[pool]
        ids = []
        for page in cognito.get_paginator("list_user_pools").paginate(MaxResults=60):
            ids += [p["Id"] for p in page["UserPools"] if p["Name"] == name]
        if len(ids) != 1:
            raise RuntimeError(f"expected exactly one user pool named {name}, found {len(ids)}")
        _pool_cache[pool] = ids[0]
    return _pool_cache[pool]


def find_client(user_pool_id, client_name):
    for page in cognito.get_paginator("list_user_pool_clients").paginate(UserPoolId=user_pool_id, MaxResults=60):
        for c in page["UserPoolClients"]:
            if c["ClientName"] == client_name:
                return c["ClientId"]
    return None


def identity_providers(user_pool_id):
    """Every IdP configured on the pool (e.g. GoogleSAML) plus local users."""
    names = []
    for page in cognito.get_paginator("list_identity_providers").paginate(UserPoolId=user_pool_id, MaxResults=60):
        names += [p["ProviderName"] for p in page["Providers"]]
    return names + ["COGNITO"]


def client_settings(user_pool_id, client_name, req):
    return {
        "UserPoolId": user_pool_id,
        "ClientName": client_name,
        "AllowedOAuthFlows": ["code"],
        "AllowedOAuthFlowsUserPoolClient": True,
        "AllowedOAuthScopes": req["scopes"],
        "CallbackURLs": req["callback_urls"],
        "LogoutURLs": req["logout_urls"],
        "SupportedIdentityProviders": identity_providers(user_pool_id),
        "ExplicitAuthFlows": ["ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"],
        "PreventUserExistenceErrors": "ENABLED",
    }


# ---------------------------------------------------------------------------
# Sync / delete
# ---------------------------------------------------------------------------

def _cred_names(team, app):
    base = f"{APPS_PREFIX}{team}/{app}"
    return f"{base}/cognito-client-id", f"{base}/cognito-client-secret"


def _put_param(name, value, secure, team, app):
    ssm.put_parameter(
        Name=name,
        Value=value,
        Type="SecureString" if secure else "String",
        Overwrite=True,
        Description=f"Managed by {PROJECT}-app-auth-sync for {team}-{app} — do not edit",
    )
    ssm.add_tags_to_resource(
        ResourceType="Parameter",
        ResourceId=name,
        Tags=[
            {"Key": "team", "Value": team},
            {"Key": "service", "Value": app},
            {"Key": "managed-by", "Value": f"{PROJECT}-app-auth-sync"},
        ],
    )


def sync(team, app, req):
    """Make Cognito match one request. Returns the client ID."""
    client_name = f"{team}-{app}"

    # A pool switch (internal <-> external) leaves no client behind.
    for other in POOL_NAMES:
        if other != req["pool"]:
            other_id = pool_id(other)
            stale = find_client(other_id, client_name)
            if stale:
                cognito.delete_user_pool_client(UserPoolId=other_id, ClientId=stale)
                _delete_client_groups(stale)
                logger.info("Removed %s from %s pool", client_name, other)

    upid = pool_id(req["pool"])
    settings = client_settings(upid, client_name, req)
    want_secret = req["client_type"] == "confidential"
    client_id = find_client(upid, client_name)
    if client_id:
        has_secret = bool(cognito.describe_user_pool_client(UserPoolId=upid, ClientId=client_id)
                          ["UserPoolClient"].get("ClientSecret"))
        if has_secret != want_secret:
            # A client's secret setting is immutable; switching type means a new client
            cognito.delete_user_pool_client(UserPoolId=upid, ClientId=client_id)
            _delete_client_groups(client_id)
            logger.info("Replacing %s: client_type changed to %s", client_name, req["client_type"])
            client_id = None
    if client_id:
        cognito.update_user_pool_client(ClientId=client_id, **settings)
        logger.info("Updated client %s (%s) in %s pool", client_name, client_id, req["pool"])
    else:
        client_id = cognito.create_user_pool_client(GenerateSecret=want_secret, **settings)["UserPoolClient"]["ClientId"]
        logger.info("Created %s client %s (%s) in %s pool", req["client_type"], client_name, client_id, req["pool"])

    id_param, secret_param = _cred_names(team, app)
    _put_param(id_param, client_id, False, team, app)
    if want_secret:
        secret = cognito.describe_user_pool_client(UserPoolId=upid, ClientId=client_id)["UserPoolClient"]["ClientSecret"]
        _put_param(secret_param, secret, True, team, app)
    else:
        ssm.delete_parameters(Names=[secret_param])
    _put_client_groups(client_id, req["allowed_groups"], team, app)
    return client_id


def _put_client_groups(client_id, groups, team, app):
    if not MEMBERSHIPS_TABLE:
        if groups:
            # Fail closed: without the table the restriction would silently vanish
            raise RuntimeError("MEMBERSHIPS_TABLE is not set; cannot record allowed groups")
        return
    dynamodb.put_item(
        TableName=MEMBERSHIPS_TABLE,
        Item={
            "pk": {"S": f"client#{client_id}"},
            "allowed_groups": {"L": [{"S": g} for g in groups]},
            "app": {"S": f"{team}-{app}"},
        },
    )


def _delete_client_groups(client_id):
    if MEMBERSHIPS_TABLE:
        dynamodb.delete_item(TableName=MEMBERSHIPS_TABLE, Key={"pk": {"S": f"client#{client_id}"}})


def remove(team, app):
    """Delete the client from every pool and its credential parameters."""
    client_name = f"{team}-{app}"
    for pool in POOL_NAMES:
        upid = pool_id(pool)
        client_id = find_client(upid, client_name)
        if client_id:
            cognito.delete_user_pool_client(UserPoolId=upid, ClientId=client_id)
            _delete_client_groups(client_id)
            logger.info("Deleted client %s from %s pool", client_name, pool)
    ssm.delete_parameters(Names=list(_cred_names(team, app)))


def read_request(name):
    try:
        return ssm.get_parameter(Name=name)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return None


def handle_change(name, operation):
    owner = parse_name(name)
    if not owner:
        logger.info("Ignoring %s: not an app auth request", name)
        return {"status": "ignored"}
    team, app = owner

    # Re-read instead of trusting the event: the parameter is the source of
    # truth, and a stale Delete event must not remove a live client.
    raw = read_request(name)
    if raw is None:
        remove(team, app)
        return {"status": "deleted", "client": f"{team}-{app}"}
    if operation == "Delete":
        logger.info("Delete event for %s but the parameter exists again — syncing", name)
    try:
        req = parse_request(raw)
    except InvalidRequest as e:
        logger.error("Invalid auth request %s: %s", name, e)
        return {"status": "invalid", "error": str(e)}
    return {"status": "synced", "client": f"{team}-{app}", "client_id": sync(team, app, req)}


def reconcile():
    """Sync every request; delete clients that are ours but have no request."""
    wanted = {}
    paginator = ssm.get_paginator("get_parameters_by_path")
    for page in paginator.paginate(Path=APPS_PREFIX, Recursive=True):
        for p in page["Parameters"]:
            owner = parse_name(p["Name"])
            if owner:
                wanted[owner] = p["Value"]

    results = {"synced": [], "invalid": [], "deleted": []}
    for (team, app), raw in sorted(wanted.items()):
        try:
            sync(team, app, parse_request(raw))
            results["synced"].append(f"{team}-{app}")
        except InvalidRequest as e:
            logger.error("Invalid auth request for %s-%s: %s", team, app, e)
            results["invalid"].append(f"{team}-{app}")

    # Orphans: clients whose credential parameter exists (so this Lambda made
    # them) but whose request is gone. Clients created by other means are
    # never touched.
    for page in paginator.paginate(Path=APPS_PREFIX, Recursive=True):
        for p in page["Parameters"]:
            m = re.match(rf"^{re.escape(APPS_PREFIX)}([a-z]+)/([a-z0-9-]+)/cognito-client-id$", p["Name"])
            if m and (m[1], m[2]) not in wanted:
                remove(m[1], m[2])
                results["deleted"].append(f"{m[1]}-{m[2]}")

    logger.info("Reconcile: %s", json.dumps(results))
    return results


def handler(event, context):
    if event.get("action") == "reconcile":
        return reconcile()

    detail = event.get("detail") or {}
    if event.get("detail-type") == "Parameter Store Change" and detail.get("name"):
        return handle_change(detail["name"], detail.get("operation", ""))

    return {"status": "ignored", "error": "unrecognised event"}
