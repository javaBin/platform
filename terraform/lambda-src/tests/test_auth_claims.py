"""Tests for auth_claims — run: python3 -m unittest discover terraform/lambda-src/tests"""

import importlib.util
import json
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


DOMAIN_ENV = ("REQUIRED_EMAIL_DOMAIN", "FEDERATED_IDENTITY_PROVIDERS", "EMAIL_VERIFIED_IDENTITY_PROVIDERS",
              "REGISTRY_GROUPS")


def load(dynamo, **env):
    for key in DOMAIN_ENV:
        os.environ.pop(key, None)
    os.environ.update(env)
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


def user(email, provider=None, verified=None, client_id="other-app"):
    """A pre-token event shaped like Cognito's: federated users carry identities."""
    attrs = {"email": email}
    e = event(email, client_id=client_id)
    if provider:
        attrs["identities"] = json.dumps([{"userId": email, "providerName": provider, "providerType": "SAML",
                                           "issuer": None, "primary": "true", "dateCreated": "1"}])
        e["userName"] = f"{provider}_{email}"
    else:
        e["userName"] = "6f1c2e4a-0000-4000-8000-000000000000"
    if verified is not None:
        attrs["email_verified"] = verified
    e["request"]["userAttributes"] = attrs
    return e


class EmailDomainTest(unittest.TestCase):
    def setUp(self):
        self.dynamo = FakeDynamo([group("helter", "ada.lovelace@java.no"), client("frosting", "helter")])
        self.h = load(self.dynamo, REQUIRED_EMAIL_DOMAIN="java.no", FEDERATED_IDENTITY_PROVIDERS="GoogleSAML")

    def tearDown(self):
        for key in DOMAIN_ENV:
            os.environ.pop(key, None)

    def assertAllowed(self, e):
        self.assertIn("claimsAndScopeOverrideDetails", self.h.handler(e, None)["response"])

    def assertDenied(self, e):
        with self.assertRaises(self.h.AccessDenied):
            self.h.handler(e, None)

    def test_existing_saml_users_pass_without_email_verified(self):
        # Live users: GoogleSAML, @java.no, email_verified=false
        self.assertAllowed(user("Ada.Lovelace@Java.NO", provider="GoogleSAML", verified="false", client_id="frosting"))

    def test_saml_user_recognised_by_username_when_identities_is_missing(self):
        e = user("ada.lovelace@java.no", provider="GoogleSAML", client_id="frosting")
        del e["request"]["userAttributes"]["identities"]
        self.assertAllowed(e)

    def test_self_registered_gmail_user_is_denied(self):
        self.assertDenied(user("someone@gmail.com", verified="true"))

    def test_lookalike_domains_are_denied(self):
        for email in ("x@java.no.evil.com", "x@notjava.no", "x@sub.java.no", "x@java.no@evil.com", "java.no", ""):
            self.assertDenied(user(email, provider="GoogleSAML"))

    def test_native_java_no_user_needs_a_verified_email(self):
        self.assertDenied(user("grace.hopper@java.no", verified="false"))
        self.assertDenied(user("grace.hopper@java.no"))
        self.assertAllowed(user("grace.hopper@java.no", verified="true"))

    def test_other_idp_needs_a_verified_email(self):
        self.assertDenied(user("grace.hopper@java.no", provider="SomeOtherIdP", verified="false"))
        self.assertAllowed(user("grace.hopper@java.no", provider="Google", verified="true"))

    def test_google_oidc_users_need_email_verified_even_when_trusted(self):
        # After cutover Google is in FEDERATED_IDENTITY_PROVIDERS; it still has
        # to vouch for the address with email_verified (mapped from Google).
        h = load(self.dynamo, REQUIRED_EMAIL_DOMAIN="java.no", FEDERATED_IDENTITY_PROVIDERS="Google")
        with self.assertRaises(h.AccessDenied):
            h.handler(user("ada.lovelace@java.no", provider="Google", verified="false"), None)
        with self.assertRaises(h.AccessDenied):
            h.handler(user("ada.lovelace@java.no", provider="Google"), None)
        h.handler(user("ada.lovelace@java.no", provider="Google", verified="true"), None)
        with self.assertRaises(h.AccessDenied):
            h.handler(user("someone@gmail.com", provider="Google", verified="true"), None)

    def test_group_rule_still_applies_after_the_domain_check(self):
        self.assertDenied(user("grace.hopper@java.no", provider="GoogleSAML", client_id="frosting"))

    def test_pool_without_a_domain_rule_accepts_any_email(self):
        h = load(self.dynamo)
        result = h.handler(user("someone@gmail.com"), None)
        self.assertEqual(groups_of(result), [])


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
        with self.assertRaises(self.h.AccessDenied), self.assertLogs(self.h.logger, "INFO") as logs:
            self.h.handler(event("grace.hopper@java.no"), None)
        # Denials log a masked address, never the full email
        self.assertNotIn("grace.hopper@java.no", "\n".join(logs.output))

    def test_client_without_restriction_accepts_anyone(self):
        result = self.h.handler(event("grace.hopper@java.no", client_id="other-app"), None)
        self.assertEqual(groups_of(result), [])

    def test_memberships_are_cached_between_invocations(self):
        self.h.handler(event("ada.lovelace@java.no"), None)
        self.h.handler(event("alan.turing@java.no"), None)
        self.assertEqual(self.dynamo.scans, 1)


class ExternalPoolTest(unittest.TestCase):
    """javabin-auth-claims-external: anyone can sign up with any email there."""

    def setUp(self):
        self.dynamo = FakeDynamo([
            group("helter", "ada.lovelace@java.no"),
            client("attendee-app", "speakers"),
        ])
        self.h = load(self.dynamo, REGISTRY_GROUPS="false")

    def tearDown(self):
        os.environ.pop("REGISTRY_GROUPS", None)

    def test_registry_groups_are_not_granted_by_email(self):
        result = self.h.handler(event("ada.lovelace@java.no", client_id="other-app"), None)
        self.assertEqual(groups_of(result), [])
        self.assertEqual(self.dynamo.scans, 0)

    def test_native_groups_still_count_for_allowed_groups(self):
        result = self.h.handler(event("x@example.com", client_id="attendee-app", native=["speakers"]), None)
        self.assertEqual(groups_of(result), ["speakers"])
        with self.assertRaises(self.h.AccessDenied):
            self.h.handler(event("ada.lovelace@java.no", client_id="attendee-app"), None)


if __name__ == "__main__":
    unittest.main()
