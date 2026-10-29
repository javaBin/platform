"""App auth sync — turns app.yaml `auth:` requests into Cognito app clients.

App Terraform (cognito-app-client module) writes the desired client config to
/{project}/apps/{team}/{app}/auth. Only team {team} can write under that path
(developer boundary, DenyBadName), so the path identifies the owner and no
caller-supplied identity is trusted.

For each request this Lambda keeps one client named "{team}-{app}" in the
requested pool, removes it from the other pool, and writes the credentials to

  /{project}/apps/{team}/{app}/cognito-client-id                 (String)
  /{project}/apps/{team}/{app}/cognito-client-secret             (SecureString)
  /{project}/apps/{team}/{app}/cognito-client-identity-provider  (String)

where the app's ECS task picks them up via its team's execution role.
Public clients (browser apps using PKCE) get no secret and no secret parameter.
These are written only when the value changes: app-secret-rollout restarts
the app on every Update, so a replaced client reaches the running tasks and
an unchanged reconcile restarts nothing.

Each sync also writes /{project}/apps/{team}/{app}/auth-status (String JSON,
team-readable, platform-written): the outcome (synced | invalid | error), the
client ID and when it last changed, sign-in IdPs, fields a reconcile had to correct (drift), and
allowed groups that do not exist in the memberships table. Teams have no
cognito-idp read access, so this is how they (and scripts/auth-doctor.py)
see what the platform did with their request.

Each pool has a fixed sign-in IdP list (INTERNAL_IDENTITY_PROVIDERS /
EXTERNAL_IDENTITY_PROVIDERS, comma-separated, set by Terraform). Every client
in the pool gets exactly that list, so with a single IdP the hosted UI sends
the user straight to it (no password form, no sign-up link). If a configured
IdP does not exist in the pool, the request is skipped and logged as an error
(status "error"); the existing client is left as it is.

An app can also request extra clients in a slot, one level down:
/{project}/apps/{team}/{app}/login-N/auth (service-routing writes these for
app.yaml routing.login, one per ALB sign-in rule). The client is then named
"{team}-{app}.login-N" ("." cannot occur in app names, so it never collides
with another app's client), and its credentials, IdP and auth-status go next
to that request.

Every client also gets a managed-login branding style (Cognito's default
look, UseCognitoProvidedValues), created only when it has none, so a style
someone customised in the console is kept. On a domain with managed login
(ManagedLoginVersion 2) a client without a style has no working sign-in
page; on the classic hosted UI (version 1) the style is unused. The style is
deleted with the client.

The request's allowed groups are written to the auth memberships table as
{pk: "client#<client_id>", allowed_groups: [...]}; the auth-claims trigger
refuses tokens for users in none of them.

Triggers:
  - EventBridge "Parameter Store Change" for */auth under /{project}/apps/
    Create/Update -> sync; Delete -> remove client + credentials
  - Direct invoke {"action": "reconcile"} -> sync every request, remove
    clients whose request is gone
"""

import datetime
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


def _idp_list(value):
    return [p.strip() for p in value.split(",") if p.strip()]


# Sign-in IdPs per pool. COGNITO means local username/password users.
POOL_IDPS = {
    "internal": _idp_list(os.environ.get("INTERNAL_IDENTITY_PROVIDERS", "GoogleSAML")),
    "external": _idp_list(os.environ.get("EXTERNAL_IDENTITY_PROVIDERS", "COGNITO")),
}
MEMBERSHIPS_TABLE = os.environ.get("MEMBERSHIPS_TABLE", "")
# Hosted-UI domains, reported in auth-status for the doctor's probes
POOL_DOMAINS = {
    "internal": os.environ.get("INTERNAL_POOL_DOMAIN", ""),
    "external": os.environ.get("EXTERNAL_POOL_DOMAIN", ""),
}
# Client fields compared for drift reporting (lists compared as sets)
# auth-status "branding": the client's managed-login style
BRANDING_CREATED, BRANDING_EXISTS = "created", "exists"
DRIFT_FIELDS = ("AllowedOAuthFlows", "AllowedOAuthFlowsUserPoolClient", "AllowedOAuthScopes", "CallbackURLs",
                "LogoutURLs", "SupportedIdentityProviders", "ExplicitAuthFlows", "PreventUserExistenceErrors")
# Standard-tier parameter limit: 4 KB (bytes, not characters)
STATUS_MAX_BYTES = 4096
# Dropped from auth-status, in order, until it fits; the doctor falls back to app.yaml
STATUS_DROPPABLE = ("callback_urls", "logout_urls", "changed_fields", "drift_corrected", "scopes",
                    "empty_groups", "allowed_groups", "unknown_groups", "identity_providers")
DEFAULT_SCOPES = ["openid", "email", "profile"]
CLIENT_TYPES = ("confidential", "public")
# Cognito allows plain http only for localhost, for local development
LOCAL_URL = re.compile(r"^http://(localhost|127\.0\.0\.1)(:\d+)?(/.*)?$")
ALLOWED_SCOPES = {"openid", "email", "profile", "phone", "aws.cognito.signin.user.admin"}
# aws.cognito.signin.user.admin lets the signed-in user call UpdateUserAttributes
# with their access token. In the internal pool, auth-claims trusts a federated
# user's email and looks up registry groups by it, so a user who could rewrite
# their own email could take another user's groups.
POOL_FORBIDDEN_SCOPES = {"internal": {"aws.cognito.signin.user.admin"}}

APPS_PREFIX = f"/{PROJECT}/apps/"
SLOT = r"login-[1-9][0-9]?"
REQUEST_RE = re.compile(
    rf"^/{re.escape(PROJECT)}/apps/(?P<team>[a-z]{{1,12}})/(?P<app>[a-z][a-z0-9-]{{0,19}})(?:/(?P<slot>{SLOT}))?/auth$"
)
CRED_ID_RE = re.compile(
    rf"^{re.escape(APPS_PREFIX)}(?P<team>[a-z]+)/(?P<app>[a-z0-9-]+)(?:/(?P<slot>{SLOT}))?/cognito-client-id$"
)

ssm = boto3.client("ssm")
cognito = boto3.client("cognito-idp")
dynamodb = boto3.client("dynamodb")


class InvalidRequest(ValueError):
    pass


class PoolMisconfigured(RuntimeError):
    """The platform side is wrong (not the app's request): skip, keep the client."""


# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------

def parse_name(name):
    """Return (team, app, slot) for a request parameter name, or None. slot is "" for the main client."""
    m = REQUEST_RE.match(name or "")
    return (m["team"], m["app"], m["slot"] or "") if m else None


def client_name(team, app, slot=""):
    return f"{team}-{app}.{slot}" if slot else f"{team}-{app}"


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
        if any("#" in u for u in value):
            # OAuth 2.0 forbids fragments in redirect URIs; Cognito rejects them
            raise InvalidRequest(f"{key} must not contain #fragments")
        if required and not value:
            raise InvalidRequest(f"{key} must not be empty")
        return value

    scopes = req.get("scopes") or DEFAULT_SCOPES
    if not isinstance(scopes, list) or not set(scopes) <= ALLOWED_SCOPES:
        raise InvalidRequest(f"scopes must be a subset of {sorted(ALLOWED_SCOPES)}")
    forbidden = set(scopes) & POOL_FORBIDDEN_SCOPES.get(pool, set())
    if forbidden:
        raise InvalidRequest(f"scopes {sorted(forbidden)} are not allowed in the {pool} pool")

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


_idp_cache = {}


def identity_providers(pool, user_pool_id):
    """The pool's configured sign-in IdPs, after checking each one exists.

    With exactly one IdP on a client, the hosted UI redirects straight to it,
    so apps need no identity_provider parameter.
    """
    wanted = POOL_IDPS[pool]
    if not wanted:
        raise PoolMisconfigured(f"no identity providers configured for the {pool} pool")
    if user_pool_id not in _idp_cache:
        names = set()
        for page in cognito.get_paginator("list_identity_providers").paginate(UserPoolId=user_pool_id, MaxResults=60):
            names |= {p["ProviderName"] for p in page["Providers"]}
        _idp_cache[user_pool_id] = names
    existing = _idp_cache[user_pool_id]
    missing = [p for p in wanted if p != "COGNITO" and p not in existing]
    if missing:
        raise PoolMisconfigured(f"{pool} pool has no identity provider {missing}; it has {sorted(existing)}")
    return list(wanted)


def primary_identity_provider(idps):
    """The IdP an app passes as identity_provider: the first federated one, else COGNITO."""
    return next((p for p in idps if p != "COGNITO"), "COGNITO")


def ensure_branding(user_pool_id, client_id):
    """Give the client a managed-login style with Cognito's defaults unless it
    has one. An existing style (default or customised) is never changed.
    Returns BRANDING_CREATED or BRANDING_EXISTS."""
    try:
        cognito.describe_managed_login_branding_by_client(UserPoolId=user_pool_id, ClientId=client_id)
        return BRANDING_EXISTS
    except cognito.exceptions.ResourceNotFoundException:
        pass
    try:
        cognito.create_managed_login_branding(UserPoolId=user_pool_id, ClientId=client_id,
                                              UseCognitoProvidedValues=True)
    except cognito.exceptions.ManagedLoginBrandingExistsException:
        return BRANDING_EXISTS
    logger.info("Created managed-login branding for client %s", client_id)
    return BRANDING_CREATED


def delete_branding(user_pool_id, client_id):
    """Delete the client's managed-login style, if it has one."""
    try:
        branding = cognito.describe_managed_login_branding_by_client(
            UserPoolId=user_pool_id, ClientId=client_id)["ManagedLoginBranding"]
        cognito.delete_managed_login_branding(UserPoolId=user_pool_id,
                                              ManagedLoginBrandingId=branding["ManagedLoginBrandingId"])
    except cognito.exceptions.ResourceNotFoundException:
        pass


def delete_client(user_pool_id, client_id):
    """Delete a client with its branding style and allowed-groups record."""
    delete_branding(user_pool_id, client_id)
    cognito.delete_user_pool_client(UserPoolId=user_pool_id, ClientId=client_id)
    _delete_client_groups(client_id)


def client_settings(pool, user_pool_id, client_name, req):
    return {
        "UserPoolId": user_pool_id,
        "ClientName": client_name,
        "AllowedOAuthFlows": ["code"],
        "AllowedOAuthFlowsUserPoolClient": True,
        "AllowedOAuthScopes": req["scopes"],
        "CallbackURLs": req["callback_urls"],
        "LogoutURLs": req["logout_urls"],
        "SupportedIdentityProviders": identity_providers(pool, user_pool_id),
        "ExplicitAuthFlows": ["ALLOW_REFRESH_TOKEN_AUTH", "ALLOW_USER_SRP_AUTH"],
        "PreventUserExistenceErrors": "ENABLED",
    }


# ---------------------------------------------------------------------------
# Sync / delete
# ---------------------------------------------------------------------------

def _base(team, app, slot=""):
    return f"{APPS_PREFIX}{team}/{app}" + (f"/{slot}" if slot else "")


def _cred_names(team, app, slot=""):
    base = _base(team, app, slot)
    return f"{base}/cognito-client-id", f"{base}/cognito-client-secret"


def _idp_name(team, app, slot=""):
    return f"{_base(team, app, slot)}/cognito-client-identity-provider"


def _status_name(team, app, slot=""):
    return f"{_base(team, app, slot)}/auth-status"


def _current(name):
    """A parameter's stored value (SecureStrings come back encrypted), or None."""
    try:
        return ssm.get_parameter(Name=name, WithDecryption=False)["Parameter"]["Value"]
    except ssm.exceptions.ParameterNotFound:
        return None


def _put_if_changed(name, value, team, app, slot=""):
    """Write a String parameter only when its value differs. Returns True if written."""
    if _current(name) == value:
        return False
    _put_param(name, value, False, team, app, slot)
    return True


def _put_param(name, value, secure, team, app, slot=""):
    ssm.put_parameter(
        Name=name,
        Value=value,
        Type="SecureString" if secure else "String",
        Overwrite=True,
        Description=f"Managed by {PROJECT}-app-auth-sync for {client_name(team, app, slot)} — do not edit",
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


def _diff(current, settings):
    """Names of DRIFT_FIELDS where the live client differs from the settings."""
    def norm(v):
        return sorted(v) if isinstance(v, list) else v
    return [f for f in DRIFT_FIELDS if norm(current.get(f, [] if isinstance(settings[f], list) else None))
            != norm(settings[f])]


def sync(team, app, req, reason="event", slot=""):
    """Make Cognito match one request. Returns a status dict (see write_status)."""
    name = client_name(team, app, slot)
    # Resolve settings first: a misconfigured pool must abort before anything
    # (including the pool-switch cleanup below) is changed.
    upid = pool_id(req["pool"])
    settings = client_settings(req["pool"], upid, name, req)
    idps = settings["SupportedIdentityProviders"]

    # A pool switch (internal <-> external) leaves no client behind.
    for other in POOL_NAMES:
        if other != req["pool"]:
            other_id = pool_id(other)
            stale = find_client(other_id, name)
            if stale:
                delete_client(other_id, stale)
                logger.info("Removed %s from %s pool", name, other)

    want_secret = req["client_type"] == "confidential"
    client_id = find_client(upid, name)
    changed = []
    if client_id:
        current = cognito.describe_user_pool_client(UserPoolId=upid, ClientId=client_id)["UserPoolClient"]
        has_secret = bool(current.get("ClientSecret"))
        changed = _diff(current, settings)
        if has_secret != want_secret:
            # A client's secret setting is immutable; switching type means a new client
            delete_client(upid, client_id)
            logger.info("Replacing %s: client_type changed to %s", name, req["client_type"])
            client_id = None
    if client_id:
        if changed and reason == "reconcile":
            # The request did not change since its last sync, so someone
            # edited the client outside the platform (e.g. the console).
            logger.warning("WARN drift on %s (%s), correcting: %s", name, client_id, ", ".join(changed))
        cognito.update_user_pool_client(ClientId=client_id, **settings)
        logger.info("Updated client %s (%s) in %s pool", name, client_id, req["pool"])
    else:
        changed = []
        client_id = cognito.create_user_pool_client(GenerateSecret=want_secret, **settings)["UserPoolClient"]["ClientId"]
        logger.info("Created %s client %s (%s) in %s pool", req["client_type"], name, client_id, req["pool"])

    # The client ID goes last: its Update is what restarts the app, and the
    # new tasks must find the matching secret and IdP already in place.
    # (app-secret-rollout ignores the secret: a new one always comes with a
    # new ID, so restarting on it too would deploy once with the old ID.)
    id_param, secret_param = _cred_names(team, app, slot)
    id_changed = _current(id_param) != client_id
    if want_secret:
        # A client's secret never changes, so a new one comes only with a new
        # client ID; skipping the rewrite needs no decrypt permission.
        if id_changed or _current(secret_param) is None:
            secret = cognito.describe_user_pool_client(UserPoolId=upid, ClientId=client_id)["UserPoolClient"]["ClientSecret"]
            _put_param(secret_param, secret, True, team, app, slot)
    elif _current(secret_param) is not None:
        ssm.delete_parameters(Names=[secret_param])
    _put_if_changed(_idp_name(team, app, slot), primary_identity_provider(idps), team, app, slot)
    if id_changed:
        _put_param(id_param, client_id, False, team, app, slot)
    _put_client_groups(client_id, req["allowed_groups"], name)
    # Last, after the credentials: a failure here must not hold back a
    # client that works on the classic hosted UI. It still fails the sync
    # (status error, alarm), since on managed login the client cannot sign in.
    branding = ensure_branding(upid, client_id)
    unknown, empty = check_groups(req["allowed_groups"])
    return {
        "status": "synced",
        "client_id": client_id,
        "client_type": req["client_type"],
        "pool": req["pool"],
        "pool_domain": POOL_DOMAINS.get(req["pool"], ""),
        "identity_providers": idps,
        "identity_provider": primary_identity_provider(idps),
        "branding": branding,
        "callback_urls": req["callback_urls"],
        "logout_urls": req["logout_urls"],
        "scopes": req["scopes"],
        "allowed_groups": req["allowed_groups"],
        "unknown_groups": unknown,
        "empty_groups": empty,
        "changed_fields": changed,
        "drift_corrected": changed if reason == "reconcile" else [],
        "client_id_changed": id_changed,
    }


def check_groups(groups):
    """(unknown, empty): allowed groups with no memberships-table entry, or no members.

    A typo in auth.groups denies everyone; the restriction is still written
    (fail closed) but reported so the team can see why.
    """
    unknown, empty = [], []
    if not MEMBERSHIPS_TABLE:
        return unknown, empty
    for g in groups:
        item = dynamodb.get_item(TableName=MEMBERSHIPS_TABLE, Key={"pk": {"S": f"group#{g}"}}).get("Item")
        if item is None:
            unknown.append(g)
        elif not (item.get("members", {}).get("L") or item.get("members", {}).get("SS")):
            empty.append(g)
    return unknown, empty


def status_value(body):
    """auth-status JSON that fits the parameter: drop STATUS_DROPPABLE fields
    until it does, never cut the JSON itself (the doctor parses it)."""
    def encode(b):
        return json.dumps(b, separators=(",", ":"))
    body = dict(body)
    value = encode(body)
    for key in STATUS_DROPPABLE:
        if len(value.encode()) <= STATUS_MAX_BYTES:
            break
        if key in body:
            body.pop(key)
            body["truncated"] = True
            value = encode(body)
    if len(value.encode()) > STATUS_MAX_BYTES:
        # Only an absurd error message gets here
        body = {k: body[k] for k in ("status", "client_id", "client_id_changed_at", "request_version",
                                     "synced_at") if k in body}
        body["truncated"] = True
        value = encode(body)
    return value


def write_status(team, app, status, request_version=None, slot=""):
    """Publish the outcome of a sync to the team-readable auth-status parameter."""
    now = datetime.datetime.now(datetime.timezone.utc).isoformat(timespec="seconds")
    body = {**status, "request_version": request_version, "synced_at": now}
    if "client_id" in status:
        # When the client ID last changed, so the doctor can tell tasks that
        # still hold the old one. Carried over from the previous status.
        changed_at = now if status.get("client_id_changed") else _previous_status(team, app, slot).get("client_id_changed_at")
        body["client_id_changed_at"] = changed_at
    try:
        _put_param(_status_name(team, app, slot), status_value(body), False, team, app, slot)
    except Exception:
        # Reporting must never turn a good sync into a failure
        logger.exception("Could not write auth-status for %s", client_name(team, app, slot))


def _previous_status(team, app, slot=""):
    try:
        prev = json.loads(_current(_status_name(team, app, slot)) or "{}")
        return prev if isinstance(prev, dict) else {}
    except Exception:
        return {}


def _put_client_groups(client_id, groups, name):
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
            "app": {"S": name},
        },
    )


def _delete_client_groups(client_id):
    if MEMBERSHIPS_TABLE:
        dynamodb.delete_item(TableName=MEMBERSHIPS_TABLE, Key={"pk": {"S": f"client#{client_id}"}})


def remove(team, app, slot=""):
    """Delete the client from every pool and its credential parameters."""
    name = client_name(team, app, slot)
    for pool in POOL_NAMES:
        upid = pool_id(pool)
        client_id = find_client(upid, name)
        if client_id:
            delete_client(upid, client_id)
            logger.info("Deleted client %s from %s pool", name, pool)
    ssm.delete_parameters(Names=[*_cred_names(team, app, slot), _idp_name(team, app, slot), _status_name(team, app, slot)])


def read_request(name):
    """(value, version) of a request parameter, or (None, None)."""
    try:
        p = ssm.get_parameter(Name=name)["Parameter"]
        return p["Value"], p.get("Version")
    except ssm.exceptions.ParameterNotFound:
        return None, None


def sync_and_report(team, app, raw, version, reason, slot=""):
    """Parse + sync one request and publish auth-status. Returns the status dict.

    Invalid requests return status "invalid"; a misconfigured pool (missing
    IdP) returns status "error" with the client untouched. Other failures are
    reported as status "error" and re-raised so the Lambda still errors.
    """
    name = client_name(team, app, slot)
    try:
        status = sync(team, app, parse_request(raw), reason, slot)
    except InvalidRequest as e:
        logger.error("Invalid auth request for %s: %s", name, e)
        status = {"status": "invalid", "error": str(e)}
    except PoolMisconfigured as e:
        logger.error("Skipped %s, client left unchanged: %s", name, e)
        status = {"status": "error", "error": str(e)}
    except Exception as e:
        write_status(team, app, {"status": "error", "error": f"{type(e).__name__}: {e}"[:500]}, version, slot)
        raise
    write_status(team, app, status, version, slot)
    return status


def handle_change(name, operation):
    owner = parse_name(name)
    if not owner:
        logger.info("Ignoring %s: not an app auth request", name)
        return {"status": "ignored"}
    team, app, slot = owner
    client = client_name(team, app, slot)

    # Re-read instead of trusting the event: the parameter is the source of
    # truth, and a stale Delete event must not remove a live client.
    raw, version = read_request(name)
    if raw is None:
        remove(team, app, slot)
        return {"status": "deleted", "client": client}
    if operation == "Delete":
        logger.info("Delete event for %s but the parameter exists again — syncing", name)
    status = sync_and_report(team, app, raw, version, "event", slot)
    if status["status"] != "synced":
        return {"status": status["status"], "error": status.get("error")}
    return {"status": "synced", "client": client, "client_id": status["client_id"]}


def reconcile():
    """Sync every request; delete clients that are ours but have no request."""
    wanted = {}
    paginator = ssm.get_paginator("get_parameters_by_path")
    for page in paginator.paginate(Path=APPS_PREFIX, Recursive=True):
        for p in page["Parameters"]:
            owner = parse_name(p["Name"])
            if owner:
                wanted[owner] = (p["Value"], p.get("Version"))

    results = {"synced": [], "invalid": [], "deleted": [], "error": [], "drift": []}
    for (team, app, slot), (raw, version) in sorted(wanted.items()):
        name = client_name(team, app, slot)
        try:
            status = sync_and_report(team, app, raw, version, "reconcile", slot)
        except Exception:
            # One broken app must not stop the others from reconciling. Not
            # re-raised: async invokes would retry the whole reconcile twice.
            # The javabin-app-auth-sync-skipped alarm counts this line.
            logger.exception("Sync failed for %s", name)
            results["error"].append(name)
            continue
        results[status["status"] if status["status"] in ("synced", "invalid") else "error"].append(name)
        if status.get("drift_corrected"):
            results["drift"].append(name)

    # Orphans: clients whose credential parameter exists (so this Lambda made
    # them) but whose request is gone. Clients created by other means are
    # never touched.
    for page in paginator.paginate(Path=APPS_PREFIX, Recursive=True):
        for p in page["Parameters"]:
            m = CRED_ID_RE.match(p["Name"])
            owner = (m["team"], m["app"], m["slot"] or "") if m else None
            if owner and owner not in wanted:
                remove(*owner)
                results["deleted"].append(client_name(*owner))

    logger.info("Reconcile: %s", json.dumps(results))
    return results


def handler(event, context):
    # IdPs can be added or removed between invocations of a warm container
    _idp_cache.clear()
    if event.get("action") == "reconcile":
        return reconcile()

    detail = event.get("detail") or {}
    if event.get("detail-type") == "Parameter Store Change" and detail.get("name"):
        return handle_change(detail["name"], detail.get("operation", ""))

    return {"status": "ignored", "error": "unrecognised event"}
