"""Tests for app_auth_sync — run: python3 -m unittest discover terraform/lambda-src/tests

boto3 is stubbed with in-memory fakes, so no AWS access or boto3 install is needed.
"""

import importlib.util
import json
import os
import sys
import types
import unittest


class ParameterNotFound(Exception):
    pass


class _Paginator:
    def __init__(self, fn, key):
        self.fn, self.key = fn, key

    def paginate(self, **kw):
        yield {self.key: self.fn(**kw)}


class FakeSSM:
    def __init__(self):
        self.params = {}
        self.tags = {}
        self.writes = []  # parameter names in write order
        self.versions = {}
        self.exceptions = types.SimpleNamespace(ParameterNotFound=ParameterNotFound)

    def get_parameter(self, Name, WithDecryption=False):
        if Name not in self.params:
            raise ParameterNotFound(Name)
        return {"Parameter": {"Name": Name, "Value": self.params[Name], "Version": self.versions.get(Name, 1)}}

    def put_parameter(self, Name, Value, Type, Overwrite, Description):
        self.params[Name] = Value
        self.versions[Name] = self.versions.get(Name, 0) + 1
        self.writes.append(Name)

    def add_tags_to_resource(self, ResourceType, ResourceId, Tags):
        self.tags[ResourceId] = {t["Key"]: t["Value"] for t in Tags}

    def delete_parameters(self, Names):
        for n in Names:
            self.params.pop(n, None)

    def get_paginator(self, name):
        assert name == "get_parameters_by_path"
        return _Paginator(lambda Path, Recursive: [
            {"Name": k, "Value": v, "Version": self.versions.get(k, 1)}
            for k, v in sorted(self.params.items()) if k.startswith(Path)
        ], "Parameters")


class FakeDynamo:
    def __init__(self):
        self.items = {}

    def put_item(self, TableName, Item):
        self.items[Item["pk"]["S"]] = Item

    def delete_item(self, TableName, Key):
        self.items.pop(Key["pk"]["S"], None)

    def get_item(self, TableName, Key):
        item = self.items.get(Key["pk"]["S"])
        return {"Item": item} if item else {}

    def add_group(self, name, members):
        self.items[f"group#{name}"] = {"pk": {"S": f"group#{name}"}, "members": {"L": [{"S": m} for m in members]}}


class FakeCognito:
    POOLS = {"pool-int": "javabin-internal", "pool-ext": "javabin-external"}

    def __init__(self, internal_idps=("GoogleSAML",)):
        self.clients = {}  # client_id -> dict
        self.n = 0
        self.internal_idps = list(internal_idps)

    def get_paginator(self, name):
        if name == "list_user_pools":
            return _Paginator(lambda MaxResults: [{"Id": i, "Name": n} for i, n in self.POOLS.items()], "UserPools")
        if name == "list_user_pool_clients":
            return _Paginator(lambda UserPoolId, MaxResults: [
                {"ClientId": cid, "ClientName": c["ClientName"]}
                for cid, c in self.clients.items() if c["UserPoolId"] == UserPoolId
            ], "UserPoolClients")
        if name == "list_identity_providers":
            return _Paginator(lambda UserPoolId, MaxResults: (
                [{"ProviderName": p} for p in self.internal_idps] if UserPoolId == "pool-int" else []
            ), "Providers")
        raise AssertionError(name)

    def create_user_pool_client(self, GenerateSecret, **settings):
        self.n += 1
        cid = f"client{self.n}"
        self.clients[cid] = {**settings, **({"ClientSecret": f"secret{self.n}"} if GenerateSecret else {})}
        return {"UserPoolClient": {"ClientId": cid}}

    def update_user_pool_client(self, ClientId, **settings):
        self.clients[ClientId].update(settings)

    def delete_user_pool_client(self, UserPoolId, ClientId):
        del self.clients[ClientId]

    def describe_user_pool_client(self, UserPoolId, ClientId):
        return {"UserPoolClient": self.clients[ClientId]}


ENV_KEYS = ("INTERNAL_IDENTITY_PROVIDERS", "EXTERNAL_IDENTITY_PROVIDERS", "INTERNAL_POOL_DOMAIN", "EXTERNAL_POOL_DOMAIN")
IDP_ENV = ENV_KEYS


def load_handler(ssm, cognito, dynamo=None, **env):
    os.environ["MEMBERSHIPS_TABLE"] = "javabin-auth-memberships"
    for key in ENV_KEYS:
        os.environ.pop(key, None)
    os.environ.update(env)
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda svc: {"ssm": ssm, "cognito-idp": cognito, "dynamodb": dynamo or FakeDynamo()}[svc]
    sys.modules["boto3"] = boto3
    path = os.path.join(os.path.dirname(__file__), "..", "app_auth_sync", "handler.py")
    spec = importlib.util.spec_from_file_location("app_auth_sync_handler", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


REQ = "/javabin/apps/cupcake/frosting/auth"
REQUEST = {"pool": "internal", "callback_urls": ["https://cupcake.javazone.no/auth/callback"],
           "logout_urls": ["https://cupcake.javazone.no/"], "scopes": ["openid", "email", "profile"]}


def event(name, operation):
    return {"detail-type": "Parameter Store Change", "detail": {"name": name, "operation": operation}}


class AppAuthSyncTest(unittest.TestCase):
    def setUp(self):
        self.ssm, self.cognito, self.dynamo = FakeSSM(), FakeCognito(), FakeDynamo()
        self.h = load_handler(self.ssm, self.cognito, self.dynamo)

    def put_request(self, req=REQUEST, name=REQ):
        self.ssm.params[name] = json.dumps(req)

    def only_client(self):
        self.assertEqual(len(self.cognito.clients), 1)
        return next(iter(self.cognito.clients.items()))

    def test_create_makes_client_and_writes_team_tagged_credentials(self):
        self.put_request()
        result = self.h.handler(event(REQ, "Create"), None)

        self.assertEqual(result["status"], "synced")
        cid, client = self.only_client()
        self.assertEqual(client["ClientName"], "cupcake-frosting")
        self.assertEqual(client["UserPoolId"], "pool-int")
        # Only the pool's IdP: no COGNITO, so no password form or sign-up link
        self.assertEqual(client["SupportedIdentityProviders"], ["GoogleSAML"])
        self.assertEqual(self.ssm.params["/javabin/apps/cupcake/frosting/cognito-client-id"], cid)
        self.assertEqual(self.ssm.params["/javabin/apps/cupcake/frosting/cognito-client-secret"], client["ClientSecret"])
        self.assertEqual(self.ssm.tags["/javabin/apps/cupcake/frosting/cognito-client-id"]["team"], "cupcake")

    def test_update_keeps_the_same_client(self):
        self.put_request()
        self.h.handler(event(REQ, "Create"), None)
        first_id, _ = self.only_client()

        self.put_request({**REQUEST, "callback_urls": ["https://new.javazone.no/cb"]})
        self.h.handler(event(REQ, "Update"), None)

        cid, client = self.only_client()
        self.assertEqual(cid, first_id)
        self.assertEqual(client["CallbackURLs"], ["https://new.javazone.no/cb"])

    def test_pool_switch_moves_the_client(self):
        self.put_request()
        self.h.handler(event(REQ, "Create"), None)
        self.put_request({**REQUEST, "pool": "external"})
        self.h.handler(event(REQ, "Update"), None)

        _, client = self.only_client()
        self.assertEqual(client["UserPoolId"], "pool-ext")
        self.assertEqual(client["SupportedIdentityProviders"], ["COGNITO"])

    def test_delete_removes_client_and_credentials(self):
        self.put_request()
        self.h.handler(event(REQ, "Create"), None)
        del self.ssm.params[REQ]

        self.assertEqual(self.h.handler(event(REQ, "Delete"), None)["status"], "deleted")
        self.assertEqual(self.cognito.clients, {})
        self.assertEqual(self.ssm.params, {})

    def test_stale_delete_event_does_not_remove_a_live_client(self):
        self.put_request()
        self.h.handler(event(REQ, "Create"), None)

        self.assertEqual(self.h.handler(event(REQ, "Delete"), None)["status"], "synced")
        self.only_client()

    def test_parameters_outside_the_auth_path_are_ignored(self):
        for name in ("/javabin/apps/cupcake/frosting/stripe-key",
                     "/javabin/platform/github-app-key",
                     "/javabin/apps/Cup-Cake/frosting/auth"):
            self.ssm.params[name] = json.dumps(REQUEST)
            self.assertEqual(self.h.handler(event(name, "Create"), None)["status"], "ignored")
        self.assertEqual(self.cognito.clients, {})

    def test_invalid_request_creates_nothing(self):
        for bad in ({**REQUEST, "pool": "both"},
                    {**REQUEST, "callback_urls": ["http://insecure"]},
                    {**REQUEST, "callback_urls": []},
                    {**REQUEST, "scopes": ["openid", "admin:everything"]}):
            self.put_request(bad)
            self.assertEqual(self.h.handler(event(REQ, "Update"), None)["status"], "invalid")
        self.assertEqual(self.cognito.clients, {})

    def test_internal_pool_refuses_the_user_admin_scope(self):
        # The scope would let a user rewrite their own email, which auth-claims
        # trusts for federated users when it looks up registry groups.
        self.put_request({**REQUEST, "pool": "internal",
                          "scopes": ["openid", "email", "aws.cognito.signin.user.admin"]})
        result = self.h.handler(event(REQ, "Create"), None)
        self.assertEqual(result["status"], "invalid")
        self.assertIn("aws.cognito.signin.user.admin", result["error"])
        self.assertEqual(self.cognito.clients, {})

    def test_external_pool_still_allows_the_user_admin_scope(self):
        self.put_request({**REQUEST, "pool": "external",
                          "scopes": ["openid", "email", "aws.cognito.signin.user.admin"]})
        self.assertEqual(self.h.handler(event(REQ, "Create"), None)["status"], "synced")

    def test_reconcile_syncs_requests_and_removes_orphans(self):
        self.put_request()
        other = "/javabin/apps/core/moresleep/auth"
        self.put_request(name=other)
        self.h.handler(event(other, "Create"), None)
        del self.ssm.params[other]  # request gone, Delete event missed

        result = self.h.handler({"action": "reconcile"}, None)

        self.assertEqual(result["synced"], ["cupcake-frosting"])
        self.assertEqual(result["deleted"], ["core-moresleep"])
        _, client = self.only_client()
        self.assertEqual(client["ClientName"], "cupcake-frosting")


class IdentityProviderTest(unittest.TestCase):
    ID_PARAM = "/javabin/apps/cupcake/frosting/cognito-client-id"

    def setUp(self):
        self.ssm, self.cognito, self.dynamo = FakeSSM(), FakeCognito(), FakeDynamo()
        self.h = load_handler(self.ssm, self.cognito, self.dynamo)
        self.ssm.params[REQ] = json.dumps(REQUEST)
        self.h.handler(event(REQ, "Create"), None)
        self.cid = next(iter(self.cognito.clients))

    def tearDown(self):
        for key in IDP_ENV:
            os.environ.pop(key, None)

    def reload(self, **idps):
        self.h = load_handler(self.ssm, self.cognito, self.dynamo, **idps)

    def client(self):
        return self.cognito.clients[self.cid]

    def test_reconcile_restores_the_pool_idp_list(self):
        self.client()["SupportedIdentityProviders"] = ["GoogleSAML", "COGNITO"]
        self.h.handler({"action": "reconcile"}, None)
        self.assertEqual(self.client()["SupportedIdentityProviders"], ["GoogleSAML"])

    def test_idp_list_comes_from_the_environment(self):
        self.reload(INTERNAL_IDENTITY_PROVIDERS="GoogleSAML, COGNITO")
        self.assertEqual(self.h.handler(event(REQ, "Update"), None)["status"], "synced")
        self.assertEqual(self.client()["SupportedIdentityProviders"], ["GoogleSAML", "COGNITO"])

    def test_missing_idp_skips_the_request_and_keeps_the_client(self):
        self.reload(INTERNAL_IDENTITY_PROVIDERS="Google")
        self.ssm.params[REQ] = json.dumps({**REQUEST, "callback_urls": ["https://new.javazone.no/cb"]})
        with self.assertLogs(level="ERROR"):
            result = self.h.handler(event(REQ, "Update"), None)
        self.assertEqual(result["status"], "error")
        self.assertIn("Google", result["error"])
        self.assertEqual(list(self.cognito.clients), [self.cid])
        self.assertEqual(self.client()["SupportedIdentityProviders"], ["GoogleSAML"])
        self.assertEqual(self.client()["CallbackURLs"], REQUEST["callback_urls"])
        self.assertEqual(self.ssm.params[self.ID_PARAM], self.cid)

    def test_missing_idp_creates_no_client(self):
        self.reload(INTERNAL_IDENTITY_PROVIDERS="Google")
        other = "/javabin/apps/core/moresleep/auth"
        self.ssm.params[other] = json.dumps(REQUEST)
        self.assertEqual(self.h.handler(event(other, "Create"), None)["status"], "error")
        self.assertEqual(list(self.cognito.clients), [self.cid])
        self.assertNotIn("/javabin/apps/core/moresleep/cognito-client-id", self.ssm.params)

    def test_empty_idp_list_is_an_error_not_a_client_nobody_can_use(self):
        self.reload(INTERNAL_IDENTITY_PROVIDERS=" , ")
        self.assertEqual(self.h.handler(event(REQ, "Update"), None)["status"], "error")
        self.assertEqual(self.client()["SupportedIdentityProviders"], ["GoogleSAML"])

    def test_missing_idp_in_target_pool_does_not_move_the_client(self):
        self.reload(EXTERNAL_IDENTITY_PROVIDERS="Google")
        self.ssm.params[REQ] = json.dumps({**REQUEST, "pool": "external"})
        self.assertEqual(self.h.handler(event(REQ, "Update"), None)["status"], "error")
        self.assertEqual(self.client()["UserPoolId"], "pool-int")

    def test_reconcile_reports_and_keeps_skipped_clients(self):
        self.reload(INTERNAL_IDENTITY_PROVIDERS="Google")
        with self.assertLogs(level="ERROR"):
            result = self.h.handler({"action": "reconcile"}, None)
        self.assertEqual(result["error"], ["cupcake-frosting"])
        self.assertEqual(result["deleted"], [])
        self.assertEqual(list(self.cognito.clients), [self.cid])
        self.assertEqual(self.ssm.params[self.ID_PARAM], self.cid)


class AuthFeaturesTest(AppAuthSyncTest):
    SECRET = "/javabin/apps/cupcake/frosting/cognito-client-secret"

    def test_public_client_has_no_secret_parameter(self):
        self.put_request({**REQUEST, "client_type": "public"})
        self.h.handler(event(REQ, "Create"), None)
        _, client = self.only_client()
        self.assertNotIn("ClientSecret", client)
        self.assertNotIn(self.SECRET, self.ssm.params)
        self.assertIn("/javabin/apps/cupcake/frosting/cognito-client-id", self.ssm.params)

    def test_switching_client_type_replaces_the_client(self):
        self.put_request()
        self.h.handler(event(REQ, "Create"), None)
        old_id, _ = self.only_client()
        self.put_request({**REQUEST, "client_type": "public"})
        self.h.handler(event(REQ, "Update"), None)
        new_id, client = self.only_client()
        self.assertNotEqual(new_id, old_id)
        self.assertNotIn("ClientSecret", client)
        self.assertNotIn(self.SECRET, self.ssm.params)
        self.assertNotIn(f"client#{old_id}", self.dynamo.items)

    def test_allowed_groups_are_recorded_and_cleaned_up(self):
        self.put_request({**REQUEST, "allowed_groups": ["helter", "pkom"]})
        self.h.handler(event(REQ, "Create"), None)
        cid, _ = self.only_client()
        item = self.dynamo.items[f"client#{cid}"]
        self.assertEqual([v["S"] for v in item["allowed_groups"]["L"]], ["helter", "pkom"])

        del self.ssm.params[REQ]
        self.h.handler(event(REQ, "Delete"), None)
        self.assertEqual(self.dynamo.items, {})

    def test_localhost_callbacks_allowed_but_not_other_http(self):
        self.put_request({**REQUEST, "callback_urls": ["https://cupcake.javazone.no/", "http://localhost:3000/cb"]})
        self.assertEqual(self.h.handler(event(REQ, "Create"), None)["status"], "synced")
        self.put_request({**REQUEST, "callback_urls": ["http://localhost.evil.com/cb"]})
        self.assertEqual(self.h.handler(event(REQ, "Update"), None)["status"], "invalid")


STATUS = "/javabin/apps/cupcake/frosting/auth-status"
CLIENT_ID = "/javabin/apps/cupcake/frosting/cognito-client-id"
IDP = "/javabin/apps/cupcake/frosting/cognito-client-identity-provider"


class SyncHardeningTest(AppAuthSyncTest):
    def status(self):
        return json.loads(self.ssm.params[STATUS])

    def test_unchanged_sync_writes_no_credentials(self):
        self.put_request()
        self.h.handler(event(REQ, "Create"), None)
        self.ssm.writes.clear()

        self.h.handler({"action": "reconcile"}, None)
        self.h.handler(event(REQ, "Update"), None)

        # Only the status is rewritten; app-secret-rollout restarts on cognito-client-* updates
        self.assertEqual(set(self.ssm.writes), {STATUS})

    def test_replaced_client_rewrites_id_and_secret(self):
        self.put_request({**REQUEST, "client_type": "public"})
        self.h.handler(event(REQ, "Create"), None)
        self.ssm.writes.clear()
        self.put_request()  # confidential: a new client with a secret
        self.h.handler(event(REQ, "Update"), None)
        secret = "/javabin/apps/cupcake/frosting/cognito-client-secret"
        # The secret lands before the ID, whose Update restarts the app
        self.assertLess(self.ssm.writes.index(secret), self.ssm.writes.index(CLIENT_ID))
        self.assertTrue(self.status()["client_id_changed"])

    def test_status_reports_sync_result_and_request_version(self):
        self.put_request({**REQUEST, "allowed_groups": ["helter"]})
        self.dynamo.add_group("helter", ["a@java.no"])
        self.h.handler(event(REQ, "Create"), None)
        cid, _ = self.only_client()
        status = self.status()
        self.assertEqual(status["status"], "synced")
        self.assertEqual(status["client_id"], cid)
        self.assertEqual(status["request_version"], 1)
        self.assertEqual(status["unknown_groups"], [])
        self.assertIn("synced_at", status)
        self.assertEqual(self.ssm.tags[STATUS]["team"], "cupcake")

    def test_invalid_request_is_reported_in_status(self):
        self.put_request({**REQUEST, "callback_urls": ["http://insecure"]})
        self.h.handler(event(REQ, "Create"), None)
        self.assertEqual(self.status()["status"], "invalid")
        self.assertIn("callback_urls", self.status()["error"])

    def test_unknown_and_empty_groups_are_reported_but_still_enforced(self):
        self.dynamo.add_group("pkom", [])
        self.put_request({**REQUEST, "allowed_groups": ["helters", "pkom"]})
        self.h.handler(event(REQ, "Create"), None)
        cid, _ = self.only_client()
        self.assertEqual(self.status()["unknown_groups"], ["helters"])
        self.assertEqual(self.status()["empty_groups"], ["pkom"])
        self.assertIn(f"client#{cid}", self.dynamo.items)

    def test_reconcile_reports_console_drift_but_request_changes_are_not_drift(self):
        self.put_request()
        self.h.handler(event(REQ, "Create"), None)
        self.put_request({**REQUEST, "logout_urls": ["https://cupcake.javazone.no/bye"]})
        self.h.handler(event(REQ, "Update"), None)
        self.assertEqual(self.status()["drift_corrected"], [])
        self.assertEqual(self.status()["changed_fields"], ["LogoutURLs"])

        cid, client = self.only_client()
        client["SupportedIdentityProviders"] = ["GoogleSAML", "COGNITO"]  # console edit
        result = self.h.handler({"action": "reconcile"}, None)
        self.assertEqual(result["drift"], ["cupcake-frosting"])
        self.assertEqual(self.status()["drift_corrected"], ["SupportedIdentityProviders"])
        self.assertEqual(client["SupportedIdentityProviders"], ["GoogleSAML"])

    def test_default_idps_and_idp_parameter(self):
        self.put_request()
        self.h.handler(event(REQ, "Create"), None)
        self.assertEqual(self.ssm.params[IDP], "GoogleSAML")
        self.assertEqual(self.status()["identity_providers"], ["GoogleSAML"])

    def test_client_id_changed_at_is_set_on_change_and_carried_over(self):
        self.put_request()
        self.h.handler(event(REQ, "Create"), None)
        first = self.status()["client_id_changed_at"]
        self.assertTrue(first)
        self.ssm.params[STATUS] = json.dumps({**self.status(), "client_id_changed_at": "2026-01-01T00:00:00+00:00"})
        self.h.handler({"action": "reconcile"}, None)
        self.assertEqual(self.status()["client_id_changed_at"], "2026-01-01T00:00:00+00:00")

    def test_fragment_in_callback_url_is_invalid(self):
        self.put_request({**REQUEST, "callback_urls": ["https://cupcake.javazone.no/#/cb"]})
        self.assertEqual(self.h.handler(event(REQ, "Create"), None)["status"], "invalid")

    def test_oversized_status_stays_valid_json_under_the_byte_limit(self):
        many = [f"https://cupcake.javazone.no/{'å' * 40}/{i}" for i in range(60)]
        value = self.h.status_value({"status": "synced", "client_id": "c1", "callback_urls": many,
                                     "logout_urls": many, "allowed_groups": ["g" * 50] * 30,
                                     "unknown_groups": ["u" * 50] * 30})
        self.assertLessEqual(len(value.encode()), self.h.STATUS_MAX_BYTES)
        body = json.loads(value)
        self.assertTrue(body["truncated"])
        self.assertEqual(body["client_id"], "c1")
        self.assertNotIn("callback_urls", body)

    def test_small_status_is_not_truncated(self):
        body = json.loads(self.h.status_value({"status": "synced", "callback_urls": ["https://a/"]}))
        self.assertNotIn("truncated", body)
        self.assertEqual(body["callback_urls"], ["https://a/"])


class PoolIdpListTest(unittest.TestCase):
    def setUp(self):
        self.ssm, self.dynamo = FakeSSM(), FakeDynamo()
        self.ssm.params[REQ] = json.dumps(REQUEST)

    def load(self, cognito, **env):
        return load_handler(self.ssm, cognito, self.dynamo, **env)

    def test_declared_list_decides_the_client_idps(self):
        cognito = FakeCognito(internal_idps=("GoogleSAML", "Google"))
        h = self.load(cognito, INTERNAL_IDENTITY_PROVIDERS="GoogleSAML", INTERNAL_POOL_DOMAIN="javabin-internal.auth.x")
        h.handler(event(REQ, "Create"), None)
        client = next(iter(cognito.clients.values()))
        self.assertEqual(client["SupportedIdentityProviders"], ["GoogleSAML"])
        self.assertEqual(json.loads(self.ssm.params[STATUS])["pool_domain"], "javabin-internal.auth.x")

    def test_cutover_switches_idp_and_updates_the_idp_parameter(self):
        cognito = FakeCognito(internal_idps=("GoogleSAML", "Google"))
        self.load(cognito, INTERNAL_IDENTITY_PROVIDERS="GoogleSAML").handler(event(REQ, "Create"), None)
        self.ssm.writes.clear()
        self.load(cognito, INTERNAL_IDENTITY_PROVIDERS="Google").handler({"action": "reconcile"}, None)
        client = next(iter(cognito.clients.values()))
        self.assertEqual(client["SupportedIdentityProviders"], ["Google"])
        self.assertEqual(self.ssm.params[IDP], "Google")
        self.assertIn(IDP, self.ssm.writes)
        self.assertNotIn(CLIENT_ID, self.ssm.writes)  # same client, no restart for the ID

    def test_declared_idp_missing_from_pool_is_reported(self):
        cognito = FakeCognito(internal_idps=("GoogleSAML",))
        h = self.load(cognito, INTERNAL_IDENTITY_PROVIDERS="Google")
        with self.assertLogs(level="ERROR"):
            self.assertEqual(h.handler(event(REQ, "Create"), None)["status"], "error")
        self.assertEqual(cognito.clients, {})
        status = json.loads(self.ssm.params[STATUS])
        self.assertEqual(status["status"], "error")
        self.assertIn("Google", status["error"])

    def test_reconcile_continues_past_a_failing_app_without_raising(self):
        # Not raised: async invokes would retry the whole reconcile twice
        cognito = FakeCognito(internal_idps=("GoogleSAML",))
        ext = "/javabin/apps/core/web/auth"
        self.ssm.params[ext] = json.dumps({**REQUEST, "pool": "external"})
        h = self.load(cognito, INTERNAL_IDENTITY_PROVIDERS="Google")
        with self.assertLogs(level="ERROR"):
            result = h.handler({"action": "reconcile"}, None)
        self.assertEqual(result["error"], ["cupcake-frosting"])
        self.assertEqual([c["ClientName"] for c in cognito.clients.values()], ["core-web"])

    def test_unexpected_failure_in_reconcile_is_logged_for_the_alarm(self):
        cognito = FakeCognito(internal_idps=("GoogleSAML",))
        h = self.load(cognito)
        cognito.create_user_pool_client = lambda **kw: (_ for _ in ()).throw(RuntimeError("boom"))
        with self.assertLogs(level="ERROR") as logs:
            result = h.handler({"action": "reconcile"}, None)
        self.assertEqual(result["error"], ["cupcake-frosting"])
        self.assertIn("Sync failed for cupcake-frosting", "\n".join(logs.output))
        self.assertEqual(json.loads(self.ssm.params[STATUS])["status"], "error")


if __name__ == "__main__":
    unittest.main()
