"""App peers sync — app-to-app calls declared in app.yaml (`calls:` / `provides:`).

App Terraform (app-network module) writes one request per app:

  /{project}/apps/{team}/{app}/network  (String JSON)
    {"version": 1, "security_group_id": "sg-…", "port": 8080,
     "calls":    {"moresleep/moresleep": ["talks.read"]},
     "provides": {"scopes": {"talks.read": "Read talks"},
                  "consumers": {"cupcake/cupcake-backend": ["talks.read"]}}}

Only team {team} can write under that path (developer boundary DenyBadName), so
the path is the app's identity; nothing in the payload is trusted for identity.
The security group in the payload must be the app's own (name
{team}-{app}-tasks, tag team={team}), or the request is ignored.

An edge caller -> callee exists when the caller lists the callee in `calls`
AND the callee lists the caller in `provides.consumers`. For each edge:

  - ingress rule on the callee's SG from the caller's SG, tcp callee port,
    tagged managed-by=javabin-app-peers-sync (rules teams add themselves are
    never touched);
  - granted scopes = requested ∩ allowed for that consumer ∩ provides.scopes,
    via a Cognito resource server "{t}-{a}" per callee and one
    client-credentials client "m2m.{t}-{a}" per caller (internal pool).

Parameters written into the caller's own path, only when the value changes
(so a reconcile never restarts apps through app-secret-rollout). They are
tagged managed-by={project}-app-peers-sync on create; a parameter without
that tag is never overwritten or deleted, whatever its name:

  peer-{t}-{a}-url     String        http://{t}-{a}.{project}.internal:{port}
  peer-{t}-{a}-scopes  String        scopes the caller's client actually holds for
                                     that callee, space-separated ("none" if none);
                                     only for calls that request scopes
  m2m-client-id        String
  m2m-client-secret    SecureString
  peers-status         String JSON   per call: active | pending-consent |
                                     callee-missing | callee-invalid

An app whose `network` request is invalid (bad payload, wrong security
group, ...) is frozen, not removed: its parameters, its managed rules and
its resource server stay as they are, so one bad apply does not cut off its
peers. The valid side of an edge can still withdraw: a frozen edge is kept
only while every valid side still declares it. Deleting the `network`
parameter removes the app for real.

Every invocation recomputes everything from all requests, so event order
and missed events do not matter.

Triggers:
  - EventBridge "Parameter Store Change" on */network (Create/Update/Delete)
  - Daily {"action": "reconcile"}: same sync plus ownership audits
    (Cloud Map names, Route53 records vs Cloud Map instances, borrowed
    security groups) reported to Slack.
"""

import json
import logging
import os
import re

import boto3

try:
    from shared.slack import get_webhook_url, post_to_slack
except ImportError:  # unit tests load the handler without shared/
    get_webhook_url = post_to_slack = None

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

PROJECT = os.environ.get("PROJECT", "javabin")
VPC_ID = os.environ.get("VPC_ID", "")
POOL_ID = os.environ.get("INTERNAL_POOL_ID", "")
NAMESPACE_ID = os.environ.get("NAMESPACE_ID", "")
HOSTED_ZONE_ID = os.environ.get("HOSTED_ZONE_ID", "")
NAMESPACE_NAME = f"{PROJECT}.internal"
CLUSTER = os.environ.get("CLUSTER", f"{PROJECT}-platform")
WEBHOOK_PARAM = os.environ.get("INFRA_WEBHOOK_PARAM", "")

MANAGED_BY = f"{PROJECT}-app-peers-sync"
APPS_PATH = f"/{PROJECT}/apps"
DEFAULT_PORT = 8000
# Suffix of the Name of resource servers this Lambda owns. Cognito allows
# only [\w\s+=,.@-] in a resource server Name.
RS_MARKER = " managed-by app.yaml"
LEGACY_RS_MARKERS = (" (app.yaml)",)  # read only, never written
CLIENT_PREFIX = "m2m."
TOKEN_MINUTES = 60

TEAM = r"[a-z]{1,12}"
APP = r"[a-z][a-z0-9-]{0,19}"
PARAM_RE = re.compile(rf"^{re.escape(APPS_PATH)}/(?P<team>{TEAM})/(?P<app>{APP})/(?P<name>[^/]+)$")
KEY_RE = re.compile(rf"^(?P<team>{TEAM})/(?P<app>{APP})$")
SCOPE_RE = re.compile(r"^[a-z][a-z0-9._-]{0,39}$")
SG_RE = re.compile(r"^sg-[0-9a-f]{8,17}$")
PEER_PARAM_RE = re.compile(rf"^peer-(?P<team>{TEAM})-(?P<app>{APP})-(?:url|scopes)$")
NO_SCOPES = "none"
MAX_CALLS, MAX_CONSUMERS, MAX_SCOPES = 20, 50, 25
MAX_CLIENT_SCOPES = 50  # Cognito: AllowedOAuthScopes holds at most 50 entries
# EC2 error codes that mean the rule to revoke is already gone
REVOKE_GONE = ("InvalidPermission.NotFound", "InvalidGroup.NotFound", "InvalidSecurityGroupRuleId.NotFound")

ssm = boto3.client("ssm")
ec2 = boto3.client("ec2")
cognito = boto3.client("cognito-idp")
servicediscovery = boto3.client("servicediscovery")
ecs = boto3.client("ecs")
route53 = boto3.client("route53")


class InvalidRequest(ValueError):
    pass


def _error_code(e):
    """The AWS error code of a botocore ClientError ('' for anything else)."""
    return (getattr(e, "response", None) or {}).get("Error", {}).get("Code", "")


def _ident(key):
    """'team/app' -> 'team-app' (unambiguous: team names have no hyphen)."""
    return key.replace("/", "-")


def _key(ident):
    """'team-app' -> 'team/app'."""
    return ident.replace("-", "/", 1)


# ---------------------------------------------------------------------------
# Request parsing
# ---------------------------------------------------------------------------

def _scopes(value, where, allowed=None):
    if not isinstance(value, list) or not all(isinstance(s, str) and SCOPE_RE.match(s) for s in value):
        raise InvalidRequest(f"{where}: expected a list of scope names")
    if allowed is not None and not set(value) <= set(allowed):
        raise InvalidRequest(f"{where}: scopes not in provides.scopes")
    return sorted(set(value))


def _refs(value, where, own, limit):
    if not isinstance(value, dict):
        raise InvalidRequest(f"{where}: expected an object")
    if len(value) > limit:
        raise InvalidRequest(f"{where}: more than {limit} entries")
    for key in value:
        if not KEY_RE.match(str(key)):
            raise InvalidRequest(f"{where}: {key!r} is not team/app")
        if key == own:
            raise InvalidRequest(f"{where}: an app cannot reference itself")
    return value


def parse_request(raw, team, app):
    """Validate a `network` parameter value written by the app-network module."""
    try:
        req = json.loads(raw)
    except (TypeError, ValueError) as e:
        raise InvalidRequest(f"not JSON: {e}")
    if not isinstance(req, dict) or req.get("version") != 1:
        raise InvalidRequest("expected a JSON object with version 1")

    sg = req.get("security_group_id")
    if not isinstance(sg, str) or not SG_RE.match(sg):
        raise InvalidRequest("security_group_id is not a security group ID")
    port = req.get("port")
    if not isinstance(port, int) or isinstance(port, bool) or not 1 <= port <= 65535:
        raise InvalidRequest("port must be 1-65535")

    own = f"{team}/{app}"
    calls = {k: _scopes(v, f"calls.{k}") for k, v in _refs(req.get("calls") or {}, "calls", own, MAX_CALLS).items()}
    total = sum(len(v) for v in calls.values())
    if total > MAX_CLIENT_SCOPES:
        raise InvalidRequest(
            f"calls: {total} scopes requested in total; one app's client can hold at most {MAX_CLIENT_SCOPES}")

    provides = req.get("provides") or {}
    if not isinstance(provides, dict):
        raise InvalidRequest("provides: expected an object")
    scopes = provides.get("scopes") or {}
    if not isinstance(scopes, dict) or len(scopes) > MAX_SCOPES:
        raise InvalidRequest(f"provides.scopes: expected an object of at most {MAX_SCOPES} scopes")
    for s, desc in scopes.items():
        if not SCOPE_RE.match(str(s)) or not isinstance(desc, str) or not 1 <= len(desc) <= 256:
            raise InvalidRequest(f"provides.scopes: invalid scope {s!r}")
    consumers = {
        k: _scopes(v, f"provides.consumers.{k}", allowed=scopes)
        for k, v in _refs(provides.get("consumers") or {}, "provides.consumers", own, MAX_CONSUMERS).items()
    }

    return {"team": team, "app": app, "key": own, "sg": sg, "port": port,
            "calls": calls, "scopes": dict(scopes), "consumers": consumers}


# ---------------------------------------------------------------------------
# State loading
# ---------------------------------------------------------------------------

def load_parameters():
    """All parameters under /{project}/apps: {name: value} (SecureStrings encrypted)."""
    params = {}
    for page in ssm.get_paginator("get_parameters_by_path").paginate(Path=APPS_PATH, Recursive=True):
        for p in page["Parameters"]:
            params[p["Name"]] = p["Value"]
    return params


def app_security_groups():
    """App task groups in the platform VPC: {sg_id: (name, team tag)}."""
    groups = {}
    filters = [{"Name": "tag:javabin-network", "Values": ["app"]}]
    if VPC_ID:
        filters.append({"Name": "vpc-id", "Values": [VPC_ID]})
    for page in ec2.get_paginator("describe_security_groups").paginate(Filters=filters):
        for g in page["SecurityGroups"]:
            tags = {t["Key"]: t["Value"] for t in g.get("Tags", [])}
            groups[g["GroupId"]] = (g["GroupName"], tags.get("team"))
    return groups


def load_requests(params, groups):
    """Return (requests {key: spec}, invalid {key: reason})."""
    requests, invalid = {}, {}
    for name, value in params.items():
        m = PARAM_RE.match(name)
        if not m or m["name"] != "network":
            continue
        team, app = m["team"], m["app"]
        key = f"{team}/{app}"
        try:
            spec = parse_request(value, team, app)
            # The payload's SG must be this app's own group, or a team could
            # make the Lambda open another team's group.
            if groups.get(spec["sg"]) != (f"{team}-{app}-tasks", team):
                raise InvalidRequest(f"{spec['sg']} is not the security group {team}-{app}-tasks of team {team}")
        except InvalidRequest as e:
            logger.warning("ignoring %s: %s", name, e)
            invalid[key] = str(e)
            continue
        requests[key] = spec
    return requests, invalid


# ---------------------------------------------------------------------------
# Desired state
# ---------------------------------------------------------------------------

def compute(requests, frozen=()):
    """Edges, grants and per-app status from all valid requests.

    `frozen` are apps whose request is invalid: a call to one is
    "callee-invalid" (its existing rule and scopes are kept elsewhere).

    Returns {
      "rules":  {(callee_sg, caller_sg, port): (caller_key, callee_team)},
      "grants": {caller_key: {callee_key: [scope, ...]}},  # only callers requesting scopes
      "status": {app_key: {...}},
      "servers": {callee_key: {scope: description}},
    }
    """
    rules, grants, status, servers = {}, {}, {}, {}
    for key, spec in requests.items():
        if spec["scopes"]:
            servers[key] = spec["scopes"]

    for caller_key, caller in requests.items():
        calls_status = {}
        for callee_key, requested in caller["calls"].items():
            callee = requests.get(callee_key)
            granted = []
            if callee is None:
                state = "callee-invalid" if callee_key in frozen else "callee-missing"
            elif caller_key not in callee["consumers"]:
                state = "pending-consent"
            else:
                state = "active"
                allowed = set(callee["consumers"][caller_key]) & set(callee["scopes"])
                granted = sorted(set(requested) & allowed)
                rules[(callee["sg"], caller["sg"], callee["port"])] = (caller_key, callee["team"])
            calls_status[callee_key] = {
                "state": state,
                "url": peer_url(callee_key, callee),
                "granted": granted,
                "denied": sorted(set(requested) - set(granted)),
            }
            if requested:
                grants.setdefault(caller_key, {})[callee_key] = granted

        consumers_status = {}
        for consumer_key in caller["consumers"]:
            consumer = requests.get(consumer_key)
            active = consumer is not None and caller_key in consumer["calls"]
            consumers_status[consumer_key] = (
                "active" if active else "caller-invalid" if consumer_key in frozen else "pending")

        if calls_status or consumers_status:
            status[caller_key] = {"calls": calls_status, "consumers": consumers_status}
    return {"rules": rules, "grants": grants, "status": status, "servers": servers}


def frozen_edge(caller_key, callee_key, requests, invalid, scope=None, port=None):
    """Whether an existing edge (or one scope of it) touching a frozen app is kept:
    at least one side is frozen and every valid side still declares it."""
    caller_frozen, callee_frozen = caller_key in invalid, callee_key in invalid
    if not (caller_frozen or callee_frozen):
        return False
    if not caller_frozen:
        calls = requests.get(caller_key, {}).get("calls", {})
        if callee_key not in calls or (scope is not None and scope not in calls[callee_key]):
            return False
    if not callee_frozen:
        callee = requests.get(callee_key)
        if callee is None or caller_key not in callee["consumers"]:
            return False
        if port is not None and port != callee["port"]:
            return False
        if scope is not None and (scope not in callee["consumers"][caller_key] or scope not in callee["scopes"]):
            return False
    return True


def peer_url(callee_key, callee):
    port = callee["port"] if callee else DEFAULT_PORT
    return f"http://{_ident(callee_key)}.{PROJECT}.internal:{port}"


# ---------------------------------------------------------------------------
# Security group rules
# ---------------------------------------------------------------------------

def managed_rules():
    """Rules this Lambda created: {(sg, referenced_sg, port): (rule_id, caller key)}."""
    rules = {}
    pages = ec2.get_paginator("describe_security_group_rules").paginate(
        Filters=[{"Name": "tag:managed-by", "Values": [MANAGED_BY]}]
    )
    for page in pages:
        for r in page["SecurityGroupRules"]:
            ref = (r.get("ReferencedGroupInfo") or {}).get("GroupId")
            if r.get("IsEgress") or not ref:
                continue
            tags = {t["Key"]: t["Value"] for t in r.get("Tags", [])}
            rules[(r["GroupId"], ref, r.get("FromPort"))] = (r["SecurityGroupRuleId"], tags.get("javabin-peer", ""))
    return rules


def sync_rules(desired, errors, requests=None, invalid=None, sg_owner=None):
    """Make managed rules match `desired`. Rules on edges touching a frozen
    (invalid) app are kept while the valid side still declares the edge;
    sg_owner maps every known app security group to its app key."""
    requests, invalid, sg_owner = requests or {}, invalid or {}, sg_owner or {}
    existing = managed_rules()
    for (sg, ref, port), (rule_id, peer) in existing.items():
        if (sg, ref, port) in desired:
            continue
        callee_key = sg_owner.get(sg)
        if callee_key and peer and frozen_edge(peer, callee_key, requests, invalid, port=port):
            logger.info("keeping %s on %s from %s (frozen edge %s -> %s)", rule_id, sg, ref, peer, callee_key)
            continue
        try:
            ec2.revoke_security_group_ingress(GroupId=sg, SecurityGroupRuleIds=[rule_id])
            logger.info("revoked %s on %s from %s port %s", rule_id, sg, ref, port)
        except Exception as e:
            if _error_code(e) in REVOKE_GONE:
                logger.info("revoke %s on %s: already gone (%s)", rule_id, sg, _error_code(e))
            else:
                # Access the callee withdrew is still open: make it visible
                logger.error("revoke %s on %s from %s port %s: %s", rule_id, sg, ref, port, e)
                errors.append(f"revoke security group rule {rule_id} {ref} -> {sg}:{port}: {e}")

    for (sg, ref, port), (caller_key, callee_team) in desired.items():
        if (sg, ref, port) in existing:
            continue
        try:
            ec2.authorize_security_group_ingress(
                GroupId=sg,
                IpPermissions=[{
                    "IpProtocol": "tcp", "FromPort": port, "ToPort": port,
                    "UserIdGroupPairs": [{"GroupId": ref, "Description": f"app.yaml peer {caller_key}"}],
                }],
                TagSpecifications=[{"ResourceType": "security-group-rule", "Tags": [
                    {"Key": "managed-by", "Value": MANAGED_BY},
                    {"Key": "javabin-peer", "Value": caller_key},
                    {"Key": "team", "Value": callee_team},
                ]}],
            )
            logger.info("authorized %s -> %s port %s", ref, sg, port)
        except Exception as e:
            if "InvalidPermission.Duplicate" in str(e):
                # The callee team already opened this itself; nothing to own.
                logger.info("rule %s -> %s port %s already exists (team-managed)", ref, sg, port)
            else:
                logger.error("authorize %s -> %s port %s: %s", ref, sg, port, e)
                errors.append(f"security group rule {ref} -> {sg}:{port}: {e}")


# ---------------------------------------------------------------------------
# Cognito: resource servers and M2M clients
# ---------------------------------------------------------------------------

def _resource_servers():
    servers, token = {}, None
    while True:
        kw = {"UserPoolId": POOL_ID, "MaxResults": 50}
        if token:
            kw["NextToken"] = token
        resp = cognito.list_resource_servers(**kw)
        for rs in resp.get("ResourceServers", []):
            servers[rs["Identifier"]] = rs
        token = resp.get("NextToken")
        if not token:
            return servers


def _m2m_clients():
    """{client name: client id} for clients this Lambda owns."""
    clients, token = {}, None
    while True:
        kw = {"UserPoolId": POOL_ID, "MaxResults": 60}
        if token:
            kw["NextToken"] = token
        resp = cognito.list_user_pool_clients(**kw)
        for c in resp.get("UserPoolClients", []):
            if c["ClientName"].startswith(CLIENT_PREFIX):
                clients[c["ClientName"]] = c["ClientId"]
        token = resp.get("NextToken")
        if not token:
            return clients


def _scope_set(rs):
    return {s["ScopeName"]: s["ScopeDescription"] for s in rs.get("Scopes", [])}


def _put_server(identifier, scopes):
    cognito.update_resource_server(
        UserPoolId=POOL_ID, Identifier=identifier, Name=f"{identifier}{RS_MARKER}",
        Scopes=[{"ScopeName": s, "ScopeDescription": d} for s, d in sorted(scopes.items())],
    )


def _client_settings(name, scopes):
    settings = {
        "UserPoolId": POOL_ID,
        "ClientName": name,
        "ExplicitAuthFlows": ["ALLOW_REFRESH_TOKEN_AUTH"],
        "AccessTokenValidity": TOKEN_MINUTES,
        "TokenValidityUnits": {"AccessToken": "minutes"},
    }
    if scopes:
        settings.update(AllowedOAuthFlows=["client_credentials"], AllowedOAuthScopes=sorted(scopes),
                        AllowedOAuthFlowsUserPoolClient=True)
    else:
        # Nothing granted yet: the client exists (so the caller's secrets
        # resolve and its tasks start) but cannot get tokens.
        settings["AllowedOAuthFlowsUserPoolClient"] = False
    return settings


def sync_cognito(plan, errors, requests=None, invalid=None):
    """Keep resource servers and M2M clients in line.

    Returns (creds {caller_key: (id, secret|None)}, clients, ours,
    held {caller_key: set of scopes its client now holds}, wanted client names).
    Scopes on frozen edges are kept (see frozen_edge); frozen callers' clients
    are only narrowed when a valid callee withdraws.
    """
    requests, invalid = requests or {}, invalid or {}
    servers = _resource_servers()
    ours = {i: rs for i, rs in servers.items() if rs.get("Name", "").endswith((RS_MARKER, *LEGACY_RS_MARKERS))}

    # 1. Create servers; widen changed ones to old ∪ new so no client loses a
    #    scope that still exists while it is being updated.
    available = set()
    for callee_key, scopes in plan["servers"].items():
        identifier = _ident(callee_key)
        try:
            if identifier not in servers:
                cognito.create_resource_server(
                    UserPoolId=POOL_ID, Identifier=identifier, Name=f"{identifier}{RS_MARKER}",
                    Scopes=[{"ScopeName": s, "ScopeDescription": d} for s, d in sorted(scopes.items())],
                )
                logger.info("created resource server %s", identifier)
            elif identifier not in ours:
                raise RuntimeError("an identifier with that name exists and is not managed by app.yaml")
            elif _scope_set(ours[identifier]) != scopes:
                _put_server(identifier, {**_scope_set(ours[identifier]), **scopes})
            available |= {f"{identifier}/{s}" for s in scopes}
        except Exception as e:
            logger.error("resource server %s: %s", identifier, e)
            errors.append(f"resource server {identifier}: {e}")

    def kept(caller_key, current):
        out = set()
        for full in current:
            ident, _, scope = full.partition("/")
            if frozen_edge(caller_key, _key(ident), requests, invalid, scope=scope):
                out.add(full)
        return out

    # 2. Clients: one per caller that requests any scope, plus frozen callers'.
    clients = _m2m_clients()
    creds, held, wanted = {}, {}, set()
    frozen_callers = [k for k in invalid if f"{CLIENT_PREFIX}{_ident(k)}" in clients]
    for caller_key in (*plan["grants"], *frozen_callers):
        per_callee = plan["grants"].get(caller_key, {})
        name = f"{CLIENT_PREFIX}{_ident(caller_key)}"
        wanted.add(name)
        scopes = {f"{_ident(k)}/{s}" for k, granted in per_callee.items() for s in granted} & available
        try:
            if name not in clients:
                resp = cognito.create_user_pool_client(GenerateSecret=True, **_client_settings(name, scopes))
                client = resp["UserPoolClient"]
                logger.info("created client %s", name)
            else:
                client = cognito.describe_user_pool_client(UserPoolId=POOL_ID, ClientId=clients[name])["UserPoolClient"]
                current = set(client.get("AllowedOAuthScopes") or []) if client.get("AllowedOAuthFlowsUserPoolClient") else set()
                scopes |= kept(caller_key, current)
                if current != scopes or client.get("AccessTokenValidity") != TOKEN_MINUTES:
                    cognito.update_user_pool_client(ClientId=client["ClientId"], **_client_settings(name, scopes))
                    logger.info("updated client %s scopes %s", name, sorted(scopes))
            held[caller_key] = scopes
            if caller_key not in invalid:  # frozen apps' parameters are not touched
                creds[caller_key] = (client["ClientId"], client.get("ClientSecret"))
        except Exception as e:
            logger.error("client %s: %s", name, e)
            errors.append(f"client {name}: {e}")

    return creds, clients, ours, held, wanted


def cleanup_cognito(plan, clients, ours, wanted_clients, errors, invalid=None):
    """Delete clients and servers no longer requested (after clients dropped
    their scopes). Frozen (invalid) callees keep their server as it is."""
    frozen_servers = {_ident(k) for k in (invalid or {})}
    for name, client_id in clients.items():
        if name not in wanted_clients:
            try:
                cognito.delete_user_pool_client(UserPoolId=POOL_ID, ClientId=client_id)
                logger.info("deleted client %s", name)
            except Exception as e:
                errors.append(f"delete client {name}: {e}")

    wanted_servers = {_ident(k): v for k, v in plan["servers"].items()}
    for identifier, rs in ours.items():
        if identifier in frozen_servers:
            continue
        try:
            if identifier not in wanted_servers:
                cognito.delete_resource_server(UserPoolId=POOL_ID, Identifier=identifier)
                logger.info("deleted resource server %s", identifier)
            elif _scope_set(rs) != wanted_servers[identifier]:
                _put_server(identifier, wanted_servers[identifier])
        except Exception as e:
            errors.append(f"resource server {identifier}: {e}")


# ---------------------------------------------------------------------------
# Parameters in the caller's path
# ---------------------------------------------------------------------------

def _owned(name, cache):
    """Whether a parameter carries this Lambda's managed-by tag. Names alone
    are no proof: a team may own a parameter with the same name."""
    if name not in cache:
        tags = ssm.list_tags_for_resource(ResourceType="Parameter", ResourceId=name).get("TagList", [])
        cache[name] = {t["Key"]: t["Value"] for t in tags}.get("managed-by") == MANAGED_BY
    return cache[name]


def _put(name, value, team, app, secure=False, exists=False, owned=None):
    kw = {"Name": name, "Value": value, "Type": "SecureString" if secure else "String",
          "Description": f"Managed by {MANAGED_BY} for {team}-{app} — do not edit"}
    if exists:
        if not _owned(name, owned if owned is not None else {}):
            raise RuntimeError(f"{name} exists and is not managed by {MANAGED_BY}; rename or delete it")
        ssm.put_parameter(Overwrite=True, **kw)
    else:
        ssm.put_parameter(Tags=[
            {"Key": "team", "Value": team},
            {"Key": "service", "Value": app},
            {"Key": "managed-by", "Value": MANAGED_BY},
        ], **kw)
    logger.info("wrote %s", name)


def sync_parameters(params, requests, invalid, plan, creds, errors, held=None):
    """Write what changed, delete what is no longer needed."""
    held = held or {}
    owned = {}  # parameter name -> carries our managed-by tag (cached per run)
    desired = {}
    for key, spec in requests.items():
        base = f"{APPS_PATH}/{key}"
        for callee_key, requested in spec["calls"].items():
            ident = _ident(callee_key)
            url_name, scopes_name = f"{base}/peer-{ident}-url", f"{base}/peer-{ident}-scopes"
            call = plan["status"][key]["calls"][callee_key]
            if call["state"] == "callee-invalid" and url_name in params:
                desired[url_name] = params[url_name]  # the frozen callee's port is unknown
            else:
                desired[url_name] = call["url"]
            if requested:
                # What the client really holds, so the caller never requests a
                # scope it was not granted (Cognito would refuse the whole token)
                if key in held:
                    have = sorted(s for s in held[key] if s.startswith(f"{ident}/"))
                    desired[scopes_name] = " ".join(have) or NO_SCOPES
                else:  # client sync failed this run
                    desired[scopes_name] = params.get(scopes_name, NO_SCOPES)
        if key in plan["status"]:
            desired[f"{base}/peers-status"] = json.dumps(plan["status"][key], sort_keys=True)
    for key, reason in invalid.items():
        desired[f"{APPS_PATH}/{key}/peers-status"] = json.dumps({"error": reason}, sort_keys=True)

    for name, value in desired.items():
        if params.get(name) == value:
            continue
        team, app = PARAM_RE.match(name).group("team", "app")
        try:
            _put(name, value, team, app, exists=name in params, owned=owned)
        except Exception as e:
            errors.append(f"write {name}: {e}")

    # Client credentials: the secret is rewritten whenever the client ID changes
    # (client recreated) or the secret parameter is missing. SecureString values
    # are not decrypted, so they are never compared. The secret is written
    # first: if that fails the ID still differs, so the next run retries.
    def cred_names(key):
        return f"{APPS_PATH}/{key}/m2m-client-id", f"{APPS_PATH}/{key}/m2m-client-secret"

    # Kept for every caller that requests scopes, even when its client sync
    # failed this run (a transient Cognito error must not break task starts)
    managed_creds = {n for key in plan["grants"] for n in cred_names(key)}
    for key, (client_id, secret) in creds.items():
        team, app = key.split("/")
        id_name, secret_name = cred_names(key)
        managed_creds |= {id_name, secret_name}
        try:
            changed = params.get(id_name) != client_id
            if secret and (changed or secret_name not in params):
                _put(secret_name, secret, team, app, secure=True, exists=secret_name in params, owned=owned)
            if changed:
                _put(id_name, client_id, team, app, exists=id_name in params, owned=owned)
        except Exception as e:
            errors.append(f"write credentials of {key}: {e}")

    # Delete managed parameters nobody needs. Invalid requests keep theirs, so
    # a broken request does not restart or break the app.
    frozen = tuple(f"{APPS_PATH}/{k}/" for k in invalid)
    candidates = [
        name for name in params
        if (m := PARAM_RE.match(name)) and _managed_name(m["name"])
        and name not in desired and name not in managed_creds
        and not (frozen and name.startswith(frozen))
    ]
    stale = []
    for name in candidates:
        try:
            if _owned(name, owned):
                stale.append(name)
            else:
                logger.info("not deleting %s: no managed-by=%s tag", name, MANAGED_BY)
        except Exception as e:
            errors.append(f"read tags of {name}: {e}")
    for i in range(0, len(stale), 10):
        try:
            ssm.delete_parameters(Names=stale[i:i + 10])
            logger.info("deleted %s", stale[i:i + 10])
        except Exception as e:
            errors.append(f"delete {stale[i:i + 10]}: {e}")


def _managed_name(name):
    return bool(PEER_PARAM_RE.match(name)) or name in ("m2m-client-id", "m2m-client-secret", "peers-status")


# ---------------------------------------------------------------------------
# Audits (reconcile only): report, never fix
# ---------------------------------------------------------------------------

def audit_cloud_map():
    """Every service in the namespace is {team}-… with a matching team tag and
    is registered only by the ECS service of the same name."""
    findings = []
    if not NAMESPACE_ID:
        return findings
    pages = servicediscovery.get_paginator("list_services").paginate(
        Filters=[{"Name": "NAMESPACE_ID", "Values": [NAMESPACE_ID], "Condition": "EQ"}]
    )
    registered = {}  # service name -> instance IPv4 addresses
    for page in pages:
        for svc in page["Services"]:
            name = svc["Name"]
            registered.setdefault(name, set())
            tags = {t["Key"]: t["Value"] for t in
                    servicediscovery.list_tags_for_resource(ResourceARN=svc["Arn"]).get("Tags", [])}
            team = tags.get("team", "")
            if not team or not name.startswith(f"{team}-"):
                findings.append(f"Cloud Map service {name} has team tag {team or '(none)'}")
            for ipage in servicediscovery.get_paginator("list_instances").paginate(ServiceId=svc["Id"]):
                for inst in ipage["Instances"]:
                    attrs = inst.get("Attributes") or {}
                    if attrs.get("AWS_INSTANCE_IPV4"):
                        registered[name].add(attrs["AWS_INSTANCE_IPV4"])
                    owner = attrs.get("ECS_SERVICE_NAME")
                    if owner != name:
                        findings.append(f"Cloud Map service {name} has an instance registered by {owner or 'a non-ECS caller'}")
    findings.extend(audit_route53(registered))
    return findings


def audit_route53(registered):
    """Every record in the namespace's private zone is an A record Cloud Map
    wrote for one of its instances (teams are denied changing the zone; this
    catches anything that slips through)."""
    findings = []
    if not HOSTED_ZONE_ID:
        return findings
    suffix = f".{NAMESPACE_NAME}"
    for page in route53.get_paginator("list_resource_record_sets").paginate(HostedZoneId=HOSTED_ZONE_ID):
        for rr in page["ResourceRecordSets"]:
            name, rtype = rr["Name"].rstrip("."), rr["Type"]
            if name == NAMESPACE_NAME and rtype in ("SOA", "NS"):
                continue
            if rtype != "A" or not name.endswith(suffix) or "AliasTarget" in rr:
                findings.append(f"unexpected {rtype} record {name} in the private zone")
                continue
            service = name[:-len(suffix)]
            values = {v["Value"] for v in rr.get("ResourceRecords", [])}
            if service not in registered:
                findings.append(f"A record {name} has no Cloud Map service")
            elif values - registered[service]:
                findings.append(f"A record {name} points at {', '.join(sorted(values - registered[service]))}, not a registered instance")
    return findings


def _task_enis():
    """{eni id: (cluster name, service name)} for ECS tasks in every cluster
    of the account (a team can run tasks in a cluster of its own)."""
    owners = {}
    for cpage in ecs.get_paginator("list_clusters").paginate():
        for cluster in cpage["clusterArns"]:
            cluster_name = cluster.rsplit("/", 1)[-1]
            task_arns = []
            # Stopped tasks still hold their ENI for a while
            for desired in ("RUNNING", "STOPPED"):
                for page in ecs.get_paginator("list_tasks").paginate(cluster=cluster, desiredStatus=desired):
                    task_arns.extend(page["taskArns"])
            for i in range(0, len(task_arns), 100):
                for task in ecs.describe_tasks(cluster=cluster, tasks=task_arns[i:i + 100])["tasks"]:
                    service = (task.get("group") or "").removeprefix("service:")
                    for att in task.get("attachments", []):
                        for d in att.get("details", []):
                            if d.get("name") == "networkInterfaceId":
                                owners[d["value"]] = (cluster_name, service)
    return owners


def audit_borrowed_groups(groups):
    """Every network interface in the VPC carrying {t}-{a}-tasks must belong
    to a task of ECS service {t}-{a} in the platform cluster."""
    findings = []
    if not groups:
        return findings
    owners = _task_enis()
    group_ids = sorted(groups)
    seen = set()
    for i in range(0, len(group_ids), 100):
        filters = [{"Name": "group-id", "Values": group_ids[i:i + 100]}]
        if VPC_ID:
            filters.append({"Name": "vpc-id", "Values": [VPC_ID]})
        for page in ec2.get_paginator("describe_network_interfaces").paginate(Filters=filters):
            for eni in page["NetworkInterfaces"]:
                eni_id = eni["NetworkInterfaceId"]
                if eni_id in seen:
                    continue
                seen.add(eni_id)
                cluster, owner = owners.get(eni_id, (None, None))
                for g in eni.get("Groups", []):
                    if g["GroupId"] not in groups:
                        continue
                    gname = groups[g["GroupId"]][0]
                    expected = gname.removesuffix("-tasks")
                    if cluster is None:
                        what = eni.get("Description") or eni.get("InterfaceType") or "no description"
                        findings.append(f"security group {gname} is attached to {eni_id} ({what}), not an ECS task")
                    elif owner != expected or cluster != CLUSTER:
                        findings.append(
                            f"security group {gname} is attached to a task of {owner or 'an unknown service'} "
                            f"in cluster {cluster}")
    return findings


def report(lines):
    if not lines or not WEBHOOK_PARAM or post_to_slack is None:
        return
    text = "*app-peers-sync* found problems with app-to-app calls:\n" + "\n".join(f"• {l}" for l in lines[:30])
    try:
        post_to_slack(get_webhook_url(ssm, WEBHOOK_PARAM),
                      {"text": text, "blocks": [{"type": "section", "text": {"type": "mrkdwn", "text": text}}]})
    except Exception as e:
        logger.error("Slack report failed: %s", e)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------

def handler(event, context):
    reconcile = (event or {}).get("action") == "reconcile"
    errors = []

    params = load_parameters()
    groups = app_security_groups()
    requests, invalid = load_requests(params, groups)
    plan = compute(requests, frozen=invalid)

    # Which app owns each security group: valid apps by their request, frozen
    # apps by the group named after them
    sg_owner = {spec["sg"]: k for k, spec in requests.items()}
    names = {f"{k.replace('/', '-')}-tasks": k for k in invalid}
    for sg, (gname, team) in groups.items():
        if gname in names and names[gname].split("/")[0] == team:
            sg_owner[sg] = names[gname]

    sync_rules(plan["rules"], errors, requests, invalid, sg_owner)
    creds, clients, ours, held, wanted = sync_cognito(plan, errors, requests, invalid)
    sync_parameters(params, requests, invalid, plan, creds, errors, held)
    cleanup_cognito(plan, clients, ours, wanted, errors, invalid)

    findings = []
    if reconcile:
        for audit in (audit_cloud_map, lambda: audit_borrowed_groups(groups)):
            try:
                findings.extend(audit())
            except Exception as e:
                logger.error("audit failed: %s", e)
                errors.append(f"audit: {e}")
        findings.extend(f"invalid request {k}: {r}" for k, r in sorted(invalid.items()))
    report(findings + errors)

    result = {
        "status": "error" if errors else "ok",
        "apps": len(requests),
        "edges": len(plan["rules"]),
        "invalid": invalid,
        "errors": errors,
        "findings": findings,
    }
    logger.info("result %s", json.dumps(result))
    return result
