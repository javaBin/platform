"""Tests for app_peers_sync — run: python3 -m unittest discover terraform/lambda-src/tests"""

import importlib.util
import json
import os
import sys
import types
import unittest

PREFIX = "/javabin/apps"


class Pager:
    def __init__(self, fn):
        self.fn = fn

    def paginate(self, **kw):
        return [self.fn(**kw)]


class ClientError(Exception):
    """Shaped like botocore's ClientError."""

    def __init__(self, code):
        super().__init__(f"An error occurred ({code})")
        self.response = {"Error": {"Code": code}}


class FakeSSM:
    def __init__(self, params=None, tags=None):
        self.params = dict(params or {})
        self.tags = {k: dict(v) for k, v in (tags or {}).items()}  # name -> {key: value}
        self.puts, self.deleted = [], []
        self.fail_puts = set()

    def get_paginator(self, name):
        assert name == "get_parameters_by_path"
        return Pager(lambda **kw: {"Parameters": [{"Name": k, "Value": v} for k, v in self.params.items()]})

    def put_parameter(self, Name, Value, Type, Description, Overwrite=False, Tags=None):
        if Name in self.params and not Overwrite:
            raise AssertionError(f"{Name} exists, Overwrite missing")
        if Overwrite and Tags:
            raise AssertionError("Tags cannot be combined with Overwrite")
        if Name in self.fail_puts:
            raise ClientError("ThrottlingException")
        self.params[Name] = Value
        if Tags:
            self.tags[Name] = {t["Key"]: t["Value"] for t in Tags}
        self.puts.append((Name, Type))

    def list_tags_for_resource(self, ResourceType, ResourceId):
        assert ResourceType == "Parameter"
        return {"TagList": [{"Key": k, "Value": v} for k, v in self.tags.get(ResourceId, {}).items()]}

    def delete_parameters(self, Names):
        for n in Names:
            self.params.pop(n, None)
            self.tags.pop(n, None)
        self.deleted.extend(Names)


class FakeEC2:
    def __init__(self, groups):
        self.groups = groups  # sg -> (name, team)
        self.rules = {}  # rule_id -> (sg, ref, port, managed, peer)
        self.authorized, self.revoked = [], []
        self.revoke_error = None
        self.enis = []  # describe_network_interfaces result
        self.n = 0

    def get_paginator(self, name):
        if name == "describe_network_interfaces":
            def enis(Filters):
                wanted = set(next(f["Values"] for f in Filters if f["Name"] == "group-id"))
                return {"NetworkInterfaces": [
                    e for e in self.enis if wanted & {g["GroupId"] for g in e["Groups"]}]}
            return Pager(enis)
        if name == "describe_security_groups":
            return Pager(lambda **kw: {"SecurityGroups": [
                {"GroupId": g, "GroupName": n, "Tags": [{"Key": "team", "Value": t}]}
                for g, (n, t) in self.groups.items()]})
        assert name == "describe_security_group_rules"
        return Pager(lambda **kw: {"SecurityGroupRules": [
            {"SecurityGroupRuleId": rid, "GroupId": sg, "IsEgress": False, "FromPort": port, "ToPort": port,
             "ReferencedGroupInfo": {"GroupId": ref}, "Tags": [{"Key": "javabin-peer", "Value": peer}]}
            for rid, (sg, ref, port, managed, peer) in self.rules.items() if managed]})

    def add_rule(self, sg, ref, port, managed=True, peer=""):
        self.n += 1
        self.rules[f"sgr-{self.n}"] = (sg, ref, port, managed, peer)

    def authorize_security_group_ingress(self, GroupId, IpPermissions, TagSpecifications):
        perm = IpPermissions[0]
        ref = perm["UserIdGroupPairs"][0]["GroupId"]
        if any(r[:3] == (GroupId, ref, perm["FromPort"]) for r in self.rules.values()):
            raise Exception("InvalidPermission.Duplicate")
        tags = {t["Key"]: t["Value"] for t in TagSpecifications[0]["Tags"]}
        assert tags["managed-by"] == "javabin-app-peers-sync"
        self.add_rule(GroupId, ref, perm["FromPort"], peer=tags["javabin-peer"])
        self.authorized.append((GroupId, ref, perm["FromPort"]))

    def revoke_security_group_ingress(self, GroupId, SecurityGroupRuleIds):
        if self.revoke_error:
            raise ClientError(self.revoke_error)
        for rid in SecurityGroupRuleIds:
            sg, ref, port, *_ = self.rules.pop(rid)
            assert sg == GroupId
            self.revoked.append((sg, ref, port))


class FakeCognito:
    def __init__(self):
        self.servers, self.clients = {}, {}  # identifier -> rs ; id -> client
        self.log = []
        self.n = 0

    def _check_scopes(self, scopes):
        for s in scopes or []:
            ident, name = s.split("/")
            assert name in {x["ScopeName"] for x in self.servers[ident]["Scopes"]}, f"unknown scope {s}"

    def list_resource_servers(self, UserPoolId, MaxResults, NextToken=None):
        return {"ResourceServers": list(self.servers.values())}

    def create_resource_server(self, UserPoolId, Identifier, Name, Scopes):
        assert Identifier not in self.servers
        self.servers[Identifier] = {"Identifier": Identifier, "Name": Name, "Scopes": Scopes}
        self.log.append(("create-rs", Identifier))

    def update_resource_server(self, UserPoolId, Identifier, Name, Scopes):
        names = {s["ScopeName"] for s in Scopes}
        for c in self.clients.values():  # Cognito refuses dropping a scope a client uses
            for s in c.get("AllowedOAuthScopes", []):
                ident, name = s.split("/")
                assert ident != Identifier or name in names, f"{s} still in use"
        self.servers[Identifier] = {"Identifier": Identifier, "Name": Name, "Scopes": Scopes}
        self.log.append(("update-rs", Identifier, sorted(names)))

    def delete_resource_server(self, UserPoolId, Identifier):
        for c in self.clients.values():
            assert not any(s.startswith(f"{Identifier}/") for s in c.get("AllowedOAuthScopes", []))
        del self.servers[Identifier]
        self.log.append(("delete-rs", Identifier))

    def list_user_pool_clients(self, UserPoolId, MaxResults, NextToken=None):
        return {"UserPoolClients": [{"ClientId": i, "ClientName": c["ClientName"]} for i, c in self.clients.items()]}

    def create_user_pool_client(self, GenerateSecret, UserPoolId, ClientName, **kw):
        self._check_scopes(kw.get("AllowedOAuthScopes"))
        self.n += 1
        cid = f"client{self.n}"
        self.clients[cid] = {"ClientId": cid, "ClientName": ClientName, "ClientSecret": f"secret{self.n}", **kw}
        self.log.append(("create-client", ClientName))
        return {"UserPoolClient": self.clients[cid]}

    def describe_user_pool_client(self, UserPoolId, ClientId):
        return {"UserPoolClient": dict(self.clients[ClientId])}

    def update_user_pool_client(self, ClientId, UserPoolId, ClientName, **kw):
        self._check_scopes(kw.get("AllowedOAuthScopes"))
        c = self.clients[ClientId]
        for k in ("AllowedOAuthScopes", "AllowedOAuthFlows"):
            c.pop(k, None)
        c.update(kw)
        self.log.append(("update-client", ClientName, sorted(kw.get("AllowedOAuthScopes", []))))

    def delete_user_pool_client(self, UserPoolId, ClientId):
        assert self.clients.pop(ClientId)
        self.log.append(("delete-client", ClientId))


class FakeServiceDiscovery:
    def __init__(self, services=()):
        self.services = list(services)  # (name, team, [ecs owners])

    def get_paginator(self, name):
        if name == "list_services":
            return Pager(lambda **kw: {"Services": [{"Id": n, "Name": n, "Arn": f"arn:{n}"} for n, _, _ in self.services]})
        def inst(o):
            owner, _, ip = o.partition("@")
            return {"Id": "i", "Attributes": {"ECS_SERVICE_NAME": owner, **({"AWS_INSTANCE_IPV4": ip} if ip else {})}}
        return Pager(lambda ServiceId: {"Instances": [
            inst(o) for n, _, owners in self.services if n == ServiceId for o in owners]})

    def list_tags_for_resource(self, ResourceARN):
        team = next(t for n, t, _ in self.services if f"arn:{n}" == ResourceARN)
        return {"Tags": [{"Key": "team", "Value": team}]}


class FakeRoute53:
    def __init__(self, records=()):
        self.records = list(records)  # (name, type, [values])

    def get_paginator(self, name):
        return Pager(lambda HostedZoneId: {"ResourceRecordSets": [
            {"Name": f"{n}.", "Type": t, "ResourceRecords": [{"Value": v} for v in vals]}
            for n, t, vals in self.records]})


class FakeECS:
    def __init__(self, tasks=()):
        # (service, eni) in javabin-platform, or (service, eni, cluster)
        self.tasks = [t if len(t) == 3 else (*t, "javabin-platform") for t in tasks]

    def _arn(self, cluster):
        return f"arn:aws:ecs:eu-central-1:1:cluster/{cluster}"

    def get_paginator(self, name):
        if name == "list_clusters":
            return Pager(lambda: {"clusterArns": sorted({self._arn(c) for *_, c in self.tasks})})
        assert name == "list_tasks"
        return Pager(lambda cluster, desiredStatus: {"taskArns": [
            f"t{i}" for i, (*_, c) in enumerate(self.tasks)
            if self._arn(c) == cluster and desiredStatus == "RUNNING"]})

    def describe_tasks(self, cluster, tasks):
        return {"tasks": [{"group": f"service:{s}", "attachments": [
            {"details": [{"name": "networkInterfaceId", "value": e}]}]}
            for i, (s, e, c) in enumerate(self.tasks) if f"t{i}" in tasks]}


def load(ssm, ec2, cognito, sd=None, ecs=None, env=None, r53=None):
    clients = {"ssm": ssm, "ec2": ec2, "cognito-idp": cognito,
               "servicediscovery": sd or FakeServiceDiscovery(), "ecs": ecs or FakeECS(),
               "route53": r53 or FakeRoute53()}
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda svc, **kw: clients[svc]
    sys.modules["boto3"] = boto3
    os.environ.update({"INTERNAL_POOL_ID": "pool", "NAMESPACE_ID": "ns-1", "HOSTED_ZONE_ID": "Z1",
                       "INFRA_WEBHOOK_PARAM": "", **(env or {})})
    path = os.path.join(os.path.dirname(__file__), "..", "app_peers_sync", "handler.py")
    spec = importlib.util.spec_from_file_location("app_peers_sync_handler", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def request(sg, port=8080, calls=None, scopes=None, consumers=None):
    return json.dumps({"version": 1, "security_group_id": sg, "port": port, "calls": calls or {},
                       "provides": {"scopes": scopes or {}, "consumers": consumers or {}}})


GROUPS = {
    "sg-0000000a": ("cupcake-cupcake-backend-tasks", "cupcake"),
    "sg-0000000b": ("moresleep-moresleep-tasks", "moresleep"),
    "sg-0000000c": ("cupcake-cupcake-tasks", "cupcake"),
}
CALLER = f"{PREFIX}/cupcake/cupcake-backend"
CALLEE = f"{PREFIX}/moresleep/moresleep"
SCOPES = {"talks.read": "Read talks", "talks.write": "Write talks"}


def world(params, groups=GROUPS, **kw):
    ssm, ec2, cognito = FakeSSM(params), FakeEC2(dict(groups)), FakeCognito()
    return load(ssm, ec2, cognito, **kw), ssm, ec2, cognito


def both_sides(caller_scopes=("talks.read", "talks.write"), consumer_scopes=("talks.read",), callee_port=8082):
    return {
        f"{CALLER}/network": request("sg-0000000a", calls={"moresleep/moresleep": list(caller_scopes)}),
        f"{CALLEE}/network": request("sg-0000000b", port=callee_port, scopes=SCOPES,
                                     consumers={"cupcake/cupcake-backend": list(consumer_scopes)}),
    }


class ConsentTest(unittest.TestCase):
    def test_edge_needs_both_sides_and_grants_the_intersection(self):
        h, ssm, ec2, cognito = world(both_sides())
        result = h.handler({}, None)
        self.assertEqual(result["status"], "ok", result)
        self.assertEqual(ec2.authorized, [("sg-0000000b", "sg-0000000a", 8082)])

        status = json.loads(ssm.params[f"{CALLER}/peers-status"])
        call = status["calls"]["moresleep/moresleep"]
        self.assertEqual((call["state"], call["granted"], call["denied"]), ("active", ["talks.read"], ["talks.write"]))
        self.assertEqual(json.loads(ssm.params[f"{CALLEE}/peers-status"])["consumers"],
                         {"cupcake/cupcake-backend": "active"})
        self.assertEqual(ssm.params[f"{CALLER}/peer-moresleep-moresleep-url"],
                         "http://moresleep-moresleep.javabin.internal:8082")
        # Only what was granted, not what was requested (talks.write is denied)
        self.assertEqual(ssm.params[f"{CALLER}/peer-moresleep-moresleep-scopes"], "moresleep-moresleep/talks.read")

        (client,) = cognito.clients.values()
        self.assertEqual(client["ClientName"], "m2m.cupcake-cupcake-backend")
        self.assertEqual(client["AllowedOAuthScopes"], ["moresleep-moresleep/talks.read"])
        self.assertEqual(client["AllowedOAuthFlows"], ["client_credentials"])
        self.assertEqual(ssm.params[f"{CALLER}/m2m-client-id"], client["ClientId"])
        self.assertIn((f"{CALLER}/m2m-client-secret", "SecureString"), ssm.puts)

    def test_caller_without_consent_gets_url_and_disabled_client_but_no_rule(self):
        params = both_sides()
        params[f"{CALLEE}/network"] = request("sg-0000000b", port=8082, scopes=SCOPES)
        h, ssm, ec2, cognito = world(params)
        h.handler({}, None)
        self.assertEqual(ec2.authorized, [])
        self.assertEqual(json.loads(ssm.params[f"{CALLER}/peers-status"])["calls"]["moresleep/moresleep"]["state"],
                         "pending-consent")
        (client,) = cognito.clients.values()
        self.assertFalse(client["AllowedOAuthFlowsUserPoolClient"])
        self.assertIn(f"{CALLER}/m2m-client-id", ssm.params)
        self.assertEqual(ssm.params[f"{CALLER}/peer-moresleep-moresleep-scopes"], "none")

    def test_unknown_callee_gets_default_port_url(self):
        params = {f"{CALLER}/network": request("sg-0000000a", calls={"moresleep/moresleep": []})}
        h, ssm, ec2, cognito = world(params)
        h.handler({}, None)
        self.assertEqual(ssm.params[f"{CALLER}/peer-moresleep-moresleep-url"],
                         "http://moresleep-moresleep.javabin.internal:8000")
        self.assertEqual(json.loads(ssm.params[f"{CALLER}/peers-status"])["calls"]["moresleep/moresleep"]["state"],
                         "callee-missing")
        self.assertEqual(cognito.clients, {})  # network-only call: no client

    def test_consumer_waiting_for_caller_is_pending(self):
        params = {f"{CALLEE}/network": request("sg-0000000b", consumers={"cupcake/cupcake-backend": []})}
        h, ssm, *_ = world(params)
        h.handler({}, None)
        self.assertEqual(json.loads(ssm.params[f"{CALLEE}/peers-status"])["consumers"],
                         {"cupcake/cupcake-backend": "pending"})


class RulesTest(unittest.TestCase):
    def test_withdrawn_consent_revokes_only_managed_rules(self):
        h, ssm, ec2, cognito = world(both_sides())
        ec2.add_rule("sg-0000000b", "sg-0000000a", 8082)              # managed, now stale
        ec2.add_rule("sg-0000000b", "sg-0000000c", 9999, managed=False)  # the team's own rule
        ssm.params[f"{CALLEE}/network"] = request("sg-0000000b", port=8082, scopes=SCOPES)
        h.handler({}, None)
        self.assertEqual(ec2.revoked, [("sg-0000000b", "sg-0000000a", 8082)])
        self.assertEqual(len(ec2.rules), 1)

    def test_port_change_replaces_the_rule(self):
        h, ssm, ec2, cognito = world(both_sides(callee_port=9000))
        ec2.add_rule("sg-0000000b", "sg-0000000a", 8082)
        h.handler({}, None)
        self.assertEqual(ec2.revoked, [("sg-0000000b", "sg-0000000a", 8082)])
        self.assertEqual(ec2.authorized, [("sg-0000000b", "sg-0000000a", 9000)])

    def test_rule_the_team_already_added_is_not_an_error(self):
        h, ssm, ec2, cognito = world(both_sides())
        ec2.add_rule("sg-0000000b", "sg-0000000a", 8082, managed=False)
        self.assertEqual(h.handler({}, None)["status"], "ok")

    def test_security_group_of_another_team_is_refused(self):
        params = both_sides()
        # moresleep claims cupcake's SG as its own to get it opened
        params[f"{CALLEE}/network"] = request("sg-0000000c", port=8082, scopes=SCOPES,
                                              consumers={"cupcake/cupcake-backend": []})
        h, ssm, ec2, cognito = world(params)
        result = h.handler({}, None)
        self.assertIn("moresleep/moresleep", result["invalid"])
        self.assertEqual(ec2.authorized, [])
        self.assertIn("error", json.loads(ssm.params[f"{CALLEE}/peers-status"]))


class CognitoTest(unittest.TestCase):
    def test_scope_removed_from_callee_is_dropped_from_client_first(self):
        h, ssm, ec2, cognito = world(both_sides(consumer_scopes=("talks.read", "talks.write")))
        h.handler({}, None)
        (client,) = cognito.clients.values()
        self.assertEqual(len(client["AllowedOAuthScopes"]), 2)

        ssm.params[f"{CALLEE}/network"] = request("sg-0000000b", port=8082, scopes={"talks.read": "Read"},
                                                  consumers={"cupcake/cupcake-backend": ["talks.read"]})
        cognito.log.clear()
        self.assertEqual(h.handler({}, None)["status"], "ok")
        self.assertEqual(client["AllowedOAuthScopes"], ["moresleep-moresleep/talks.read"])
        self.assertEqual(cognito.log[-1], ("update-rs", "moresleep-moresleep", ["talks.read"]))

    def test_callee_gone_deletes_server_after_client_lost_scopes(self):
        h, ssm, ec2, cognito = world(both_sides())
        h.handler({}, None)
        del ssm.params[f"{CALLEE}/network"]
        self.assertEqual(h.handler({}, None)["status"], "ok")
        self.assertEqual(cognito.servers, {})
        (client,) = cognito.clients.values()
        self.assertFalse(client["AllowedOAuthFlowsUserPoolClient"])

    def test_foreign_resource_server_is_left_alone(self):
        h, ssm, ec2, cognito = world(both_sides())
        cognito.servers["moresleep-moresleep"] = {"Identifier": "moresleep-moresleep", "Name": "hand-made", "Scopes": []}
        cognito.servers["other"] = {"Identifier": "other", "Name": "other", "Scopes": []}
        result = h.handler({}, None)
        self.assertEqual(result["status"], "error")
        self.assertIn("other", cognito.servers)
        self.assertEqual(cognito.servers["moresleep-moresleep"]["Name"], "hand-made")

    def test_caller_removed_deletes_client_and_parameters(self):
        h, ssm, ec2, cognito = world(both_sides())
        h.handler({}, None)
        del ssm.params[f"{CALLER}/network"]
        h.handler({}, None)
        self.assertEqual(cognito.clients, {})
        self.assertFalse([n for n in ssm.params if n.startswith(f"{CALLER}/")])
        self.assertEqual(ec2.rules, {})


class ParametersTest(unittest.TestCase):
    def test_second_run_writes_nothing(self):
        h, ssm, ec2, cognito = world(both_sides())
        h.handler({}, None)
        ssm.puts.clear()
        cognito.log.clear()
        h.handler({"action": "reconcile"}, None)
        self.assertEqual(ssm.puts, [])
        self.assertEqual(cognito.log, [])

    def test_invalid_request_keeps_existing_credentials(self):
        h, ssm, ec2, cognito = world(both_sides())
        h.handler({}, None)
        ssm.params[f"{CALLER}/network"] = "not json"
        h.handler({}, None)
        self.assertIn(f"{CALLER}/m2m-client-id", ssm.params)
        self.assertIn(f"{CALLER}/peer-moresleep-moresleep-url", ssm.params)
        self.assertEqual(len(cognito.clients), 1)

    def test_callee_narrowing_scopes_updates_the_scopes_parameter(self):
        h, ssm, ec2, cognito = world(both_sides(consumer_scopes=("talks.read", "talks.write")))
        h.handler({}, None)
        self.assertEqual(ssm.params[f"{CALLER}/peer-moresleep-moresleep-scopes"],
                         "moresleep-moresleep/talks.read moresleep-moresleep/talks.write")
        ssm.params[f"{CALLEE}/network"] = request("sg-0000000b", port=8082, scopes=SCOPES,
                                                  consumers={"cupcake/cupcake-backend": ["talks.read"]})
        h.handler({}, None)
        self.assertEqual(ssm.params[f"{CALLER}/peer-moresleep-moresleep-scopes"], "moresleep-moresleep/talks.read")

    def test_parse_rejects_bad_requests(self):
        h, *_ = world({})
        bad = [
            "[]",
            json.dumps({"version": 2}),
            request("sg-zzz"),
            request("sg-0000000a", port=0),
            request("sg-0000000a", calls={"cupcake/cupcake-backend": []}),          # self
            request("sg-0000000a", calls={"Cupcake/x": []}),
            request("sg-0000000a", calls={"a/b": ["bad scope"]}),
            request("sg-0000000a", scopes={"a": "A"}, consumers={"x/y": ["b"]}),   # not provided
        ]
        for raw in bad:
            with self.assertRaises(h.InvalidRequest, msg=raw):
                h.parse_request(raw, "cupcake", "cupcake-backend")


class FrozenTest(unittest.TestCase):
    """An invalid request freezes the app; it does not cut off its peers."""

    def test_invalid_callee_keeps_rule_server_and_caller_scopes(self):
        h, ssm, ec2, cognito = world(both_sides())
        h.handler({}, None)
        ssm.puts.clear()
        ssm.params[f"{CALLEE}/network"] = "not json"
        result = h.handler({}, None)
        self.assertIn("moresleep/moresleep", result["invalid"])
        self.assertEqual(ec2.revoked, [])
        self.assertEqual(len(ec2.rules), 1)
        self.assertIn("moresleep-moresleep", cognito.servers)
        (client,) = cognito.clients.values()
        self.assertEqual(client["AllowedOAuthScopes"], ["moresleep-moresleep/talks.read"])
        self.assertEqual(ssm.params[f"{CALLER}/peer-moresleep-moresleep-url"],
                         "http://moresleep-moresleep.javabin.internal:8082")
        self.assertEqual(ssm.params[f"{CALLER}/peer-moresleep-moresleep-scopes"], "moresleep-moresleep/talks.read")
        self.assertEqual(json.loads(ssm.params[f"{CALLER}/peers-status"])["calls"]["moresleep/moresleep"]["state"],
                         "callee-invalid")
        # Only the status parameters changed
        self.assertEqual({n for n, _ in ssm.puts}, {f"{CALLER}/peers-status", f"{CALLEE}/peers-status"})

        # Fixed again: nothing to redo
        ssm.params[f"{CALLEE}/network"] = both_sides()[f"{CALLEE}/network"]
        ec2.authorized.clear()
        self.assertEqual(h.handler({}, None)["status"], "ok")
        self.assertEqual(ec2.authorized, [])

    def test_valid_caller_can_still_withdraw_from_invalid_callee(self):
        h, ssm, ec2, cognito = world(both_sides())
        h.handler({}, None)
        ssm.params[f"{CALLEE}/network"] = "not json"
        ssm.params[f"{CALLER}/network"] = request("sg-0000000a")
        h.handler({}, None)
        self.assertEqual(ec2.rules, {})
        self.assertEqual(cognito.clients, {})
        self.assertIn("moresleep-moresleep", cognito.servers)  # frozen callee's own server stays

    def test_valid_callee_can_still_withdraw_from_invalid_caller(self):
        h, ssm, ec2, cognito = world(both_sides())
        h.handler({}, None)
        ssm.params[f"{CALLER}/network"] = "not json"
        ssm.params[f"{CALLEE}/network"] = request("sg-0000000b", port=8082, scopes=SCOPES)
        self.assertEqual(h.handler({}, None)["status"], "ok")
        self.assertEqual(ec2.rules, {})
        (client,) = cognito.clients.values()  # kept, but without the withdrawn scope
        self.assertFalse(client["AllowedOAuthFlowsUserPoolClient"])

    def test_invalid_caller_keeps_its_edge(self):
        h, ssm, ec2, cognito = world(both_sides())
        h.handler({}, None)
        ssm.params[f"{CALLER}/network"] = "not json"
        h.handler({}, None)
        self.assertEqual(ec2.revoked, [])
        (client,) = cognito.clients.values()
        self.assertEqual(client["AllowedOAuthScopes"], ["moresleep-moresleep/talks.read"])
        self.assertEqual(json.loads(ssm.params[f"{CALLEE}/peers-status"])["consumers"],
                         {"cupcake/cupcake-backend": "caller-invalid"})

    def test_deleted_request_is_removed_for_real(self):
        h, ssm, ec2, cognito = world(both_sides())
        h.handler({}, None)
        del ssm.params[f"{CALLEE}/network"]
        h.handler({}, None)
        self.assertEqual(ec2.rules, {})
        self.assertEqual(cognito.servers, {})
        self.assertEqual(ssm.params[f"{CALLER}/peer-moresleep-moresleep-scopes"], "none")


class SafetyTest(unittest.TestCase):
    def test_resource_server_name_uses_only_characters_cognito_accepts(self):
        h, ssm, ec2, cognito = world(both_sides())
        h.handler({}, None)
        self.assertRegex(cognito.servers["moresleep-moresleep"]["Name"], r"^[\w\s+=,.@-]+$")

    def test_resource_server_with_the_old_marker_is_still_ours(self):
        h, ssm, ec2, cognito = world(both_sides())
        cognito.servers["moresleep-moresleep"] = {"Identifier": "moresleep-moresleep",
                                                  "Name": "moresleep-moresleep (app.yaml)", "Scopes": []}
        self.assertEqual(h.handler({}, None)["status"], "ok")
        self.assertEqual(len(cognito.servers["moresleep-moresleep"]["Scopes"]), 2)

    def test_failed_revoke_is_reported(self):
        h, ssm, ec2, cognito = world(both_sides())
        ec2.add_rule("sg-0000000b", "sg-0000000a", 9999)  # stale
        ec2.revoke_error = "UnauthorizedOperation"
        result = h.handler({}, None)
        self.assertEqual(result["status"], "error")
        self.assertTrue(any("revoke" in e for e in result["errors"]), result["errors"])

    def test_revoke_of_a_rule_already_gone_is_fine(self):
        h, ssm, ec2, cognito = world(both_sides())
        ec2.add_rule("sg-0000000b", "sg-0000000a", 9999)
        ec2.revoke_error = "InvalidPermission.NotFound"
        self.assertEqual(h.handler({}, None)["status"], "ok")

    def test_transient_cognito_failure_keeps_credentials(self):
        h, ssm, ec2, cognito = world(both_sides())
        h.handler({}, None)

        def fail(**kw):
            raise ClientError("TooManyRequestsException")
        cognito.describe_user_pool_client = fail
        result = h.handler({}, None)
        self.assertEqual(result["status"], "error")
        self.assertIn(f"{CALLER}/m2m-client-id", ssm.params)
        self.assertIn(f"{CALLER}/m2m-client-secret", ssm.params)

    def test_failed_secret_write_leaves_the_old_id_so_the_next_run_retries(self):
        h, ssm, ec2, cognito = world(both_sides())
        h.handler({}, None)
        old_id = ssm.params[f"{CALLER}/m2m-client-id"]
        cognito.clients.clear()  # client gone: the next run recreates it
        ssm.fail_puts.add(f"{CALLER}/m2m-client-secret")
        self.assertEqual(h.handler({}, None)["status"], "error")
        self.assertEqual(ssm.params[f"{CALLER}/m2m-client-id"], old_id)

        ssm.fail_puts.clear()
        self.assertEqual(h.handler({}, None)["status"], "ok")
        (client,) = cognito.clients.values()
        self.assertEqual(ssm.params[f"{CALLER}/m2m-client-id"], client["ClientId"])
        self.assertEqual(ssm.params[f"{CALLER}/m2m-client-secret"], client["ClientSecret"])

    def test_parameters_without_our_tag_are_never_deleted(self):
        user = {f"{CALLER}/m2m-client-secret": "team secret", f"{CALLER}/peer-x-y-url": "mine"}
        params = {f"{CALLER}/network": request("sg-0000000a"), **user}
        ssm, ec2, cognito = FakeSSM(params, tags={f"{CALLER}/peer-x-y-url": {"managed-by": "terraform"}}), \
            FakeEC2(dict(GROUPS)), FakeCognito()
        h = load(ssm, ec2, cognito)
        self.assertEqual(h.handler({}, None)["status"], "ok")
        self.assertEqual(ssm.deleted, [])
        for name, value in user.items():
            self.assertEqual(ssm.params[name], value)

    def test_parameter_without_our_tag_is_not_overwritten(self):
        params = both_sides()
        params[f"{CALLER}/peers-status"] = "the team's own"
        h, ssm, ec2, cognito = world(params)
        result = h.handler({}, None)
        self.assertEqual(result["status"], "error")
        self.assertEqual(ssm.params[f"{CALLER}/peers-status"], "the team's own")
        self.assertTrue(any("not managed by" in e for e in result["errors"]), result["errors"])

    def test_more_scopes_than_one_client_can_hold_is_invalid(self):
        h, *_ = world({})
        many = [f"s{i}" for i in range(20)]
        raw = request("sg-0000000a", calls={f"t/a{i}": many for i in range(3)})
        with self.assertRaisesRegex(h.InvalidRequest, "at most 50"):
            h.parse_request(raw, "cupcake", "cupcake-backend")
        ok = request("sg-0000000a", calls={"t/a0": many, "t/a1": many, "t/a2": many[:10]})
        h.parse_request(ok, "cupcake", "cupcake-backend")


class AuditTest(unittest.TestCase):
    def test_reconcile_reports_squatting_and_borrowed_groups(self):
        sd = FakeServiceDiscovery([
            ("moresleep-moresleep", "cupcake", ["moresleep-moresleep"]),   # wrong team tag
            ("cupcake-cupcake", "cupcake", ["evil-thing"]),                # foreign registration
            ("cupcake-cupcake-backend", "cupcake", ["cupcake-cupcake-backend"]),
        ])
        ecs = FakeECS([("evil-thing", "eni-1"), ("cupcake-cupcake-backend", "eni-2")])
        h, ssm, ec2, cognito = world({}, sd=sd, ecs=ecs)
        ec2.enis = [
            {"NetworkInterfaceId": "eni-1", "Groups": [{"GroupId": "sg-0000000a"}]},
            {"NetworkInterfaceId": "eni-2", "Groups": [{"GroupId": "sg-0000000a"}]},
        ]
        findings = h.handler({"action": "reconcile"}, None)["findings"]
        self.assertEqual(len(findings), 3, findings)
        self.assertTrue(any("evil-thing" in f and "security group" in f for f in findings))

    def test_reconcile_reports_records_cloud_map_did_not_write(self):
        sd = FakeServiceDiscovery([("cupcake-cupcake", "cupcake", ["cupcake-cupcake@10.0.1.5"])])
        r53 = FakeRoute53([
            ("javabin.internal", "SOA", ["x"]), ("javabin.internal", "NS", ["x"]),
            ("cupcake-cupcake.javabin.internal", "A", ["10.0.1.5"]),        # fine
            ("cupcake-cupcake.javabin.internal", "A", ["10.0.9.9"]),        # hijacked
            ("moresleep-moresleep.javabin.internal", "A", ["10.0.9.9"]),    # no service
            ("x.javabin.internal", "CNAME", ["evil.example.com"]),
        ])
        h, *_ = world({}, sd=sd, r53=r53)
        findings = h.handler({"action": "reconcile"}, None)["findings"]
        self.assertEqual(len(findings), 3, findings)
        self.assertTrue(any("10.0.9.9" in f and "cupcake-cupcake" in f for f in findings))

    def test_borrowed_group_outside_the_platform_cluster_is_reported(self):
        ecs = FakeECS([
            ("cupcake-cupcake-backend", "eni-1"),                          # fine
            ("cupcake-cupcake-backend", "eni-2", "moresleep-own-cluster"),  # same name, other cluster
        ])
        h, ssm, ec2, cognito = world({}, ecs=ecs)
        ec2.enis = [
            {"NetworkInterfaceId": "eni-1", "Groups": [{"GroupId": "sg-0000000a"}]},
            {"NetworkInterfaceId": "eni-2", "Groups": [{"GroupId": "sg-0000000a"}]},
            # Not an ECS task at all (e.g. a Lambda or an instance)
            {"NetworkInterfaceId": "eni-3", "Description": "AWS Lambda VPC ENI-evil",
             "Groups": [{"GroupId": "sg-0000000b"}]},
        ]
        findings = h.audit_borrowed_groups(dict(GROUPS))
        self.assertEqual(len(findings), 2, findings)
        self.assertTrue(any("moresleep-own-cluster" in f for f in findings))
        self.assertTrue(any("eni-3" in f and "moresleep-moresleep-tasks" in f for f in findings))

    def test_audits_only_run_on_reconcile(self):
        sd = FakeServiceDiscovery([("moresleep-moresleep", "cupcake", [])])
        h, *_ = world({}, sd=sd)
        self.assertEqual(h.handler({}, None)["findings"], [])


if __name__ == "__main__":
    unittest.main()
