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
(so a reconcile never restarts apps through app-secret-rollout):

  peer-{t}-{a}-url   String        http://{t}-{a}.{project}.internal:{port}
  m2m-client-id      String
  m2m-client-secret  SecureString
  peers-status       String JSON   per call: active | pending-consent | callee-missing

Every invocation recomputes everything from all requests, so event order
and missed events do not matter.

Triggers:
  - EventBridge "Parameter Store Change" on */network (Create/Update/Delete)
  - Daily {"action": "reconcile"}: same sync plus ownership audits
    (Cloud Map names, borrowed security groups) reported to Slack.
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
CLUSTER = os.environ.get("CLUSTER", f"{PROJECT}-platform")
WEBHOOK_PARAM = os.environ.get("INFRA_WEBHOOK_PARAM", "")

MANAGED_BY = f"{PROJECT}-app-peers-sync"
APPS_PATH = f"/{PROJECT}/apps"
DEFAULT_PORT = 8000
RS_MARKER = " (app.yaml)"
CLIENT_PREFIX = "m2m."
TOKEN_MINUTES = 60

TEAM = r"[a-z]{1,12}"
APP = r"[a-z][a-z0-9-]{0,19}"
PARAM_RE = re.compile(rf"^{re.escape(APPS_PATH)}/(?P<team>{TEAM})/(?P<app>{APP})/(?P<name>[^/]+)$")
KEY_RE = re.compile(rf"^(?P<team>{TEAM})/(?P<app>{APP})$")
SCOPE_RE = re.compile(r"^[a-z][a-z0-9._-]{0,39}$")
SG_RE = re.compile(r"^sg-[0-9a-f]{8,17}$")
PEER_URL_RE = re.compile(rf"^peer-(?P<team>{TEAM})-(?P<app>{APP})-url$")
MAX_CALLS, MAX_CONSUMERS, MAX_SCOPES = 20, 50, 25

ssm = boto3.client("ssm")
ec2 = boto3.client("ec2")
cognito = boto3.client("cognito-idp")
servicediscovery = boto3.client("servicediscovery")
ecs = boto3.client("ecs")


class InvalidRequest(ValueError):
    pass


def _ident(key):
    """'team/app' -> 'team-app' (unambiguous: team names have no hyphen)."""
    return key.replace("/", "-")


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

def compute(requests):
    """Edges, grants and per-app status from all valid requests.

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
                state = "callee-missing"
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
            consumers_status[consumer_key] = "active" if active else "pending"

        if calls_status or consumers_status:
            status[caller_key] = {"calls": calls_status, "consumers": consumers_status}
    return {"rules": rules, "grants": grants, "status": status, "servers": servers}


def peer_url(callee_key, callee):
    port = callee["port"] if callee else DEFAULT_PORT
    return f"http://{_ident(callee_key)}.{PROJECT}.internal:{port}"


# ---------------------------------------------------------------------------
# Security group rules
# ---------------------------------------------------------------------------

def managed_rules():
    """Rules this Lambda created: {(sg, referenced_sg, port): (rule_id, sg)}."""
    rules = {}
    pages = ec2.get_paginator("describe_security_group_rules").paginate(
        Filters=[{"Name": "tag:managed-by", "Values": [MANAGED_BY]}]
    )
    for page in pages:
        for r in page["SecurityGroupRules"]:
            ref = (r.get("ReferencedGroupInfo") or {}).get("GroupId")
            if r.get("IsEgress") or not ref:
                continue
            rules[(r["GroupId"], ref, r.get("FromPort"))] = r["SecurityGroupRuleId"]
    return rules


def sync_rules(desired, errors):
    existing = managed_rules()
    for (sg, ref, port), rule_id in existing.items():
        if (sg, ref, port) in desired:
            continue
        try:
            ec2.revoke_security_group_ingress(GroupId=sg, SecurityGroupRuleIds=[rule_id])
            logger.info("revoked %s on %s from %s port %s", rule_id, sg, ref, port)
        except Exception as e:  # group or rule already gone
            logger.info("revoke %s on %s: %s", rule_id, sg, e)

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


def sync_cognito(plan, errors):
    """Keep resource servers and M2M clients in line. Returns {caller_key: (id, secret|None)}."""
    servers = _resource_servers()
    ours = {i: rs for i, rs in servers.items() if rs.get("Name", "").endswith(RS_MARKER)}

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

    # 2. Clients: one per caller that requests any scope.
    clients = _m2m_clients()
    creds = {}
    for caller_key, per_callee in plan["grants"].items():
        name = f"{CLIENT_PREFIX}{_ident(caller_key)}"
        scopes = {f"{_ident(k)}/{s}" for k, granted in per_callee.items() for s in granted} & available
        try:
            if name not in clients:
                resp = cognito.create_user_pool_client(GenerateSecret=True, **_client_settings(name, scopes))
                client = resp["UserPoolClient"]
                logger.info("created client %s", name)
            else:
                client = cognito.describe_user_pool_client(UserPoolId=POOL_ID, ClientId=clients[name])["UserPoolClient"]
                current = set(client.get("AllowedOAuthScopes") or []) if client.get("AllowedOAuthFlowsUserPoolClient") else set()
                if current != scopes or client.get("AccessTokenValidity") != TOKEN_MINUTES:
                    cognito.update_user_pool_client(ClientId=client["ClientId"], **_client_settings(name, scopes))
                    logger.info("updated client %s scopes %s", name, sorted(scopes))
            creds[caller_key] = (client["ClientId"], client.get("ClientSecret"))
        except Exception as e:
            logger.error("client %s: %s", name, e)
            errors.append(f"client {name}: {e}")

    return creds, clients, ours


def cleanup_cognito(plan, clients, ours, keep_callers, errors):
    """Delete clients and servers no longer requested (after clients dropped their scopes)."""
    wanted_clients = {f"{CLIENT_PREFIX}{_ident(k)}" for k in (*plan["grants"], *keep_callers)}
    for name, client_id in clients.items():
        if name not in wanted_clients:
            try:
                cognito.delete_user_pool_client(UserPoolId=POOL_ID, ClientId=client_id)
                logger.info("deleted client %s", name)
            except Exception as e:
                errors.append(f"delete client {name}: {e}")

    wanted_servers = {_ident(k): v for k, v in plan["servers"].items()}
    for identifier, rs in ours.items():
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

def _put(name, value, team, app, secure=False, exists=False):
    kw = {"Name": name, "Value": value, "Type": "SecureString" if secure else "String",
          "Description": f"Managed by {MANAGED_BY} for {team}-{app} — do not edit"}
    if exists:
        ssm.put_parameter(Overwrite=True, **kw)
    else:
        ssm.put_parameter(Tags=[
            {"Key": "team", "Value": team},
            {"Key": "service", "Value": app},
            {"Key": "managed-by", "Value": MANAGED_BY},
        ], **kw)
    logger.info("wrote %s", name)


def sync_parameters(params, requests, invalid, plan, creds, errors):
    """Write what changed, delete what is no longer needed."""
    desired = {}
    for key, spec in requests.items():
        base = f"{APPS_PATH}/{key}"
        for callee_key in spec["calls"]:
            desired[f"{base}/peer-{_ident(callee_key)}-url"] = plan["status"][key]["calls"][callee_key]["url"]
        if key in plan["status"]:
            desired[f"{base}/peers-status"] = json.dumps(plan["status"][key], sort_keys=True)
    for key, reason in invalid.items():
        desired[f"{APPS_PATH}/{key}/peers-status"] = json.dumps({"error": reason}, sort_keys=True)

    for name, value in desired.items():
        if params.get(name) == value:
            continue
        team, app = PARAM_RE.match(name).group("team", "app")
        try:
            _put(name, value, team, app, exists=name in params)
        except Exception as e:
            errors.append(f"write {name}: {e}")

    # Client credentials: the secret is rewritten whenever the client ID changes
    # (client recreated) or the secret parameter is missing. SecureString values
    # are not decrypted, so they are never compared.
    managed_creds = set()
    for key, (client_id, secret) in creds.items():
        team, app = key.split("/")
        id_name, secret_name = f"{APPS_PATH}/{key}/m2m-client-id", f"{APPS_PATH}/{key}/m2m-client-secret"
        managed_creds |= {id_name, secret_name}
        try:
            changed = params.get(id_name) != client_id
            if changed:
                _put(id_name, client_id, team, app, exists=id_name in params)
            if secret and (changed or secret_name not in params):
                _put(secret_name, secret, team, app, secure=True, exists=secret_name in params)
        except Exception as e:
            errors.append(f"write credentials of {key}: {e}")

    # Delete managed parameters nobody needs. Invalid requests keep theirs, so
    # a broken request does not restart or break the app.
    frozen = tuple(f"{APPS_PATH}/{k}/" for k in invalid)
    stale = [
        name for name in params
        if (m := PARAM_RE.match(name)) and _managed_name(m["name"])
        and name not in desired and name not in managed_creds
        and not (frozen and name.startswith(frozen))
    ]
    for i in range(0, len(stale), 10):
        try:
            ssm.delete_parameters(Names=stale[i:i + 10])
            logger.info("deleted %s", stale[i:i + 10])
        except Exception as e:
            errors.append(f"delete {stale[i:i + 10]}: {e}")


def _managed_name(name):
    return bool(PEER_URL_RE.match(name)) or name in ("m2m-client-id", "m2m-client-secret", "peers-status")


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
    for page in pages:
        for svc in page["Services"]:
            name = svc["Name"]
            tags = {t["Key"]: t["Value"] for t in
                    servicediscovery.list_tags_for_resource(ResourceARN=svc["Arn"]).get("Tags", [])}
            team = tags.get("team", "")
            if not team or not name.startswith(f"{team}-"):
                findings.append(f"Cloud Map service {name} has team tag {team or '(none)'}")
            for ipage in servicediscovery.get_paginator("list_instances").paginate(ServiceId=svc["Id"]):
                for inst in ipage["Instances"]:
                    owner = (inst.get("Attributes") or {}).get("ECS_SERVICE_NAME")
                    if owner != name:
                        findings.append(f"Cloud Map service {name} has an instance registered by {owner or 'a non-ECS caller'}")
    return findings


def audit_borrowed_groups(groups):
    """A task carrying {t}-{a}-tasks must belong to ECS service {t}-{a}."""
    findings = []
    task_arns = []
    for page in ecs.get_paginator("list_tasks").paginate(cluster=CLUSTER):
        task_arns.extend(page["taskArns"])
    eni_owner = {}
    for i in range(0, len(task_arns), 100):
        for task in ecs.describe_tasks(cluster=CLUSTER, tasks=task_arns[i:i + 100])["tasks"]:
            service = (task.get("group") or "").removeprefix("service:")
            for att in task.get("attachments", []):
                for d in att.get("details", []):
                    if d.get("name") == "networkInterfaceId":
                        eni_owner[d["value"]] = service
    enis = list(eni_owner)
    for i in range(0, len(enis), 100):
        resp = ec2.describe_network_interfaces(NetworkInterfaceIds=enis[i:i + 100])
        for eni in resp["NetworkInterfaces"]:
            owner = eni_owner.get(eni["NetworkInterfaceId"], "")
            for g in eni.get("Groups", []):
                if g["GroupId"] not in groups:
                    continue
                expected = groups[g["GroupId"]][0].removesuffix("-tasks")
                if owner != expected:
                    findings.append(f"security group {groups[g['GroupId']][0]} is attached to a task of {owner or 'an unknown service'}")
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
    plan = compute(requests)

    sync_rules(plan["rules"], errors)
    creds, clients, ours = sync_cognito(plan, errors)
    # Callers with invalid requests keep their client until fixed
    keep = [k for k in invalid if f"{CLIENT_PREFIX}{_ident(k)}" in clients]
    sync_parameters(params, requests, invalid, plan, creds, errors)
    cleanup_cognito(plan, clients, ours, keep, errors)

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
