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
        self.exceptions = types.SimpleNamespace(ParameterNotFound=ParameterNotFound)

    def get_parameter(self, Name):
        if Name not in self.params:
            raise ParameterNotFound(Name)
        return {"Parameter": {"Name": Name, "Value": self.params[Name]}}

    def put_parameter(self, Name, Value, Type, Overwrite, Description):
        self.params[Name] = Value

    def add_tags_to_resource(self, ResourceType, ResourceId, Tags):
        self.tags[ResourceId] = {t["Key"]: t["Value"] for t in Tags}

    def delete_parameters(self, Names):
        for n in Names:
            self.params.pop(n, None)

    def get_paginator(self, name):
        assert name == "get_parameters_by_path"
        return _Paginator(lambda Path, Recursive: [
            {"Name": k, "Value": v} for k, v in sorted(self.params.items()) if k.startswith(Path)
        ], "Parameters")


class FakeDynamo:
    def __init__(self):
        self.items = {}

    def put_item(self, TableName, Item):
        self.items[Item["pk"]["S"]] = Item

    def delete_item(self, TableName, Key):
        self.items.pop(Key["pk"]["S"], None)


class FakeCognito:
    POOLS = {"pool-int": "javabin-internal", "pool-ext": "javabin-external"}

    def __init__(self):
        self.clients = {}  # client_id -> dict
        self.n = 0

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
                [{"ProviderName": "GoogleSAML"}] if UserPoolId == "pool-int" else []
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


def load_handler(ssm, cognito, dynamo=None):
    os.environ["MEMBERSHIPS_TABLE"] = "javabin-auth-memberships"
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
        self.assertEqual(client["SupportedIdentityProviders"], ["GoogleSAML", "COGNITO"])
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


if __name__ == "__main__":
    unittest.main()
