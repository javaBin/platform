"""Tests for auth_claims — run: python3 -m unittest discover terraform/lambda-src/tests"""

import importlib.util
import os
import sys
import types
import unittest


class FakeDynamo:
    def __init__(self, items):
        self.items = {i["pk"]["S"]: i for i in items}
        self.scans = 0

    def scan(self, TableName, **kw):
        self.scans += 1
        return {"Items": list(self.items.values())}

    def get_item(self, TableName, Key):
        item = self.items.get(Key["pk"]["S"])
        return {"Item": item} if item else {}


def group(name, *emails):
    return {"pk": {"S": f"group#{name}"}, "members": {"L": [{"S": e} for e in emails]}}


def client(cid, *groups):
    return {"pk": {"S": f"client#{cid}"}, "allowed_groups": {"L": [{"S": g} for g in groups]}}


def load(dynamo):
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda svc: dynamo
    sys.modules["boto3"] = boto3
    path = os.path.join(os.path.dirname(__file__), "..", "auth_claims", "handler.py")
    spec = importlib.util.spec_from_file_location("auth_claims_handler", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def event(email, client_id="frosting", native=()):
    return {
        "callerContext": {"clientId": client_id},
        "request": {"userAttributes": {"email": email},
                    "groupConfiguration": {"groupsToOverride": list(native)}},
        "response": {},
    }


def groups_of(result):
    return result["response"]["claimsAndScopeOverrideDetails"]["groupOverrideDetails"]["groupsToOverride"]


class AuthClaimsTest(unittest.TestCase):
    def setUp(self):
        self.dynamo = FakeDynamo([
            group("helter", "ada.lovelace@java.no", "alan.turing@java.no"),
            group("team-cupcake", "ada.lovelace@java.no"),
            client("frosting", "helter"),
        ])
        self.h = load(self.dynamo)

    def test_registry_groups_go_into_the_token_case_insensitively(self):
        result = self.h.handler(event("Ada.Lovelace@java.no"), None)
        self.assertEqual(groups_of(result), ["helter", "team-cupcake"])

    def test_native_cognito_groups_are_kept(self):
        result = self.h.handler(event("alan.turing@java.no", native=["admins"]), None)
        self.assertEqual(groups_of(result), ["admins", "helter"])

    def test_user_outside_allowed_groups_is_refused(self):
        with self.assertRaises(self.h.AccessDenied):
            self.h.handler(event("grace.hopper@java.no"), None)

    def test_client_without_restriction_accepts_anyone(self):
        result = self.h.handler(event("grace.hopper@java.no", client_id="other-app"), None)
        self.assertEqual(groups_of(result), [])

    def test_memberships_are_cached_between_invocations(self):
        self.h.handler(event("ada.lovelace@java.no"), None)
        self.h.handler(event("alan.turing@java.no"), None)
        self.assertEqual(self.dynamo.scans, 1)


if __name__ == "__main__":
    unittest.main()
