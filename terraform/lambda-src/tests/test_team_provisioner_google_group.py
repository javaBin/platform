"""Tests for team_provisioner Google group role mapping and settings enforcement.

Run: python3 -m unittest discover -s terraform/lambda-src/tests
"""

import importlib.util
import io
import os
import sys
import types
import unittest
import urllib.error
from unittest import mock

SRC = os.path.join(os.path.dirname(__file__), "..")

IN_SYNC = {
    "whoCanViewGroup": "ALL_MEMBERS_CAN_VIEW",
    "isArchived": "true",
    "archiveOnly": "false",
    "whoCanViewMembership": "ALL_IN_DOMAIN_CAN_VIEW",
    "whoCanJoin": "INVITED_CAN_JOIN",
    "allowWebPosting": "true",
    "allowExternalMembers": "false",
    "whoCanPostMessage": "ANYONE_CAN_POST",
    "messageModerationLevel": "MODERATE_NONE",
}


def load():
    os.environ.setdefault("MEMBERSHIPS_TABLE", "javabin-auth-memberships")
    os.environ.setdefault("COGNITO_INTERNAL_POOL_ID", "pool")
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda svc, **kw: mock.MagicMock()
    sys.modules["boto3"] = boto3
    sys.path.insert(0, SRC)
    sys.path.insert(0, os.path.join(SRC, "team_provisioner"))
    spec = importlib.util.spec_from_file_location(
        "team_provisioner_handler_gg", os.path.join(SRC, "team_provisioner", "handler.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.time.sleep = lambda s: None
    return mod


def http_error(code, body):
    return urllib.error.HTTPError("https://x", code, "err", {}, io.BytesIO(body.encode()))


class FakeSettings:
    """Stands in for _groups_settings_api; records writes."""

    def __init__(self, state, not_found_first=0):
        self.state = dict(state) if state is not None else None
        self.not_found_first = not_found_first
        self.writes = []

    def __call__(self, method, email, token, body=None):
        if method == "GET":
            if self.not_found_first:
                self.not_found_first -= 1
                return None
            return dict(self.state) if self.state is not None else None
        self.writes.append(body)
        self.state.update(body)
        return dict(self.state)


class FakeDirectory:
    """Stands in for _google_api for one group; records calls."""

    def __init__(self, members):
        self.members = dict(members)  # email -> role
        self.calls = []

    def __call__(self, method, path, token, body=None):
        self.calls.append((method, path, body))
        if method == "GET" and "/members" in path:
            return {"members": [{"email": e, "role": r} for e, r in self.members.items()]}
        if method == "GET":
            return {"email": "team-x@java.no"}
        return {}

    def writes(self, method):
        return [c for c in self.calls if c[0] == method and "/members" in c[1]]


class EnforceSettingsTest(unittest.TestCase):
    def setUp(self):
        self.h = load()
        self.h._get_google_groups_settings_token = lambda: "tok"

    def test_new_group_gets_all_settings(self):
        fake = FakeSettings({"whoCanPostMessage": "ANYONE_CAN_POST"})
        self.h._groups_settings_api = fake
        result = self.h.enforce_team_group_settings("team-x@java.no")
        self.assertEqual(fake.writes, [self.h.TEAM_GROUP_SETTINGS])
        self.assertEqual(result["changed"], sorted(self.h.TEAM_GROUP_SETTINGS))
        self.assertEqual(result["current"]["whoCanPostMessage"], "ANYONE_CAN_POST")

    def test_in_sync_group_gets_no_write(self):
        fake = FakeSettings(IN_SYNC)
        self.h._groups_settings_api = fake
        result = self.h.enforce_team_group_settings("team-x@java.no")
        self.assertEqual(fake.writes, [])
        self.assertEqual(result["changed"], [])

    def test_partial_drift_writes_only_drifted_keys(self):
        fake = FakeSettings({**IN_SYNC, "whoCanViewGroup": "ALL_MANAGERS_CAN_VIEW", "isArchived": "false"})
        self.h._groups_settings_api = fake
        self.h.enforce_team_group_settings("team-x@java.no")
        self.assertEqual(fake.writes, [{"whoCanViewGroup": "ALL_MEMBERS_CAN_VIEW", "isArchived": "true"}])

    def test_post_and_moderation_settings_are_never_written(self):
        fake = FakeSettings({"whoCanPostMessage": "ALL_MEMBERS_CAN_POST", "messageModerationLevel": "MODERATE_ALL_MESSAGES"})
        self.h._groups_settings_api = fake
        self.h.enforce_team_group_settings("team-x@java.no")
        for w in fake.writes:
            self.assertNotIn("whoCanPostMessage", w)
            self.assertNotIn("messageModerationLevel", w)
        self.assertEqual(fake.state["whoCanPostMessage"], "ALL_MEMBERS_CAN_POST")

    def test_not_found_then_found_retries(self):
        fake = FakeSettings(IN_SYNC, not_found_first=2)
        self.h._groups_settings_api = fake
        result = self.h.enforce_team_group_settings("team-x@java.no")
        self.assertEqual(result["changed"], [])

    def test_missing_scope_skips(self):
        def no_scope():
            raise self.h.GoogleScopeNotDelegated("scope")
        self.h._get_google_groups_settings_token = no_scope
        self.h._groups_settings_api = mock.Mock(side_effect=AssertionError("must not call"))
        result = self.h.enforce_team_group_settings("team-x@java.no")
        self.assertEqual(result, {"skipped": True, "reason": "groups settings scope not delegated"})

    def test_api_disabled_gives_clear_error(self):
        body = '{"error":{"code":403,"errors":[{"reason":"accessNotConfigured"}]}}'
        with mock.patch.object(self.h.urllib.request, "urlopen", side_effect=http_error(403, body)):
            result = self.h.enforce_team_group_settings("team-x@java.no")
        self.assertIn("not enabled in GCP project javabin-platform", result["error"])


class DelegatedTokenTest(unittest.TestCase):
    def test_unauthorized_client_raises_scope_not_delegated(self):
        h = load()
        h._create_jwt = lambda payload, key: "jwt"
        err = http_error(401, '{"error":"unauthorized_client","error_description":"Client is unauthorized"}')
        with mock.patch.object(h, "_get_ssm_param", side_effect=lambda n: '{"client_email": "sa@x", "private_key": "k"}' if n == h.GOOGLE_SA_PARAM else "admin@java.no"), \
                mock.patch.object(h.urllib.request, "urlopen", side_effect=err):
            with self.assertRaises(h.GoogleScopeNotDelegated):
                h._get_google_groups_settings_token()


class SyncGoogleGroupRolesTest(unittest.TestCase):
    def setUp(self):
        self.h = load()
        self.h._get_google_access_token = lambda: "tok"
        self.h.enforce_team_group_settings = lambda email: {"changed": []}

    def sync(self, current, members):
        fake = FakeDirectory(current)
        self.h._google_api = fake
        result = self.h.sync_google_group({"name": "x", "description": "d", "members": members})
        return fake, result

    def test_maintainer_added_as_manager(self):
        fake, result = self.sync({}, [{"google": "ada", "github": "ada", "role": "maintainer"}, "bob"])
        posts = {c[2]["email"]: c[2]["role"] for c in fake.writes("POST")}
        self.assertEqual(posts, {"ada@java.no": "MANAGER", "bob@java.no": "MEMBER"})
        self.assertEqual(result["added"], 2)

    def test_role_promotion_and_demotion(self):
        fake, result = self.sync(
            {"ada@java.no": "MEMBER", "bob@java.no": "MANAGER"},
            [{"google": "ada", "role": "maintainer"}, {"google": "bob", "role": "member"}],
        )
        patches = {c[1].rsplit("/", 1)[1]: c[2]["role"] for c in fake.writes("PATCH")}
        self.assertEqual(patches, {"ada%40java.no": "MANAGER", "bob%40java.no": "MEMBER"})
        self.assertEqual(len(result["role_changes"]), 2)
        self.assertEqual(fake.writes("POST"), [])

    def test_matching_roles_cause_no_writes(self):
        fake, _ = self.sync({"ada@java.no": "MANAGER"}, [{"google": "ada", "role": "maintainer"}])
        self.assertEqual(fake.writes("PATCH") + fake.writes("POST") + fake.writes("DELETE"), [])

    def test_owner_is_never_changed(self):
        fake, _ = self.sync({"ada@java.no": "OWNER"}, [{"google": "ada", "role": "member"}])
        self.assertEqual(fake.writes("PATCH"), [])

    def test_members_not_in_registry_are_removed(self):
        fake, _ = self.sync({"ada@java.no": "MEMBER", "gone@java.no": "MANAGER"}, ["ada"])
        self.assertEqual([c[1] for c in fake.writes("DELETE")], ["/groups/team-x%40java.no/members/gone%40java.no"])

    def test_settings_result_is_included(self):
        _, result = self.sync({}, ["ada"])
        self.assertEqual(result["settings"], {"changed": []})


class ResultLineTest(unittest.TestCase):
    def test_skipped_settings_shows_warning_but_still_synced(self):
        h = load()
        line = h._result_line("google", {"synced": True, "member_count": 2, "settings": {
            "skipped": True, "reason": "groups settings scope not delegated"}})
        self.assertIn(":white_check_mark: google: synced", line)
        self.assertIn(":warning: group settings skipped", line)

    def test_changes_are_listed(self):
        h = load()
        line = h._result_line("google", {"synced": True, "member_count": 2, "role_changes": ["a"],
                                         "settings": {"changed": ["isArchived"]}})
        self.assertIn("1 role change(s)", line)
        self.assertIn("settings: isArchived", line)


if __name__ == "__main__":
    unittest.main()
