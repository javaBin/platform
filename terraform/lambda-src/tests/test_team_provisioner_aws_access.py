"""Tests for team_provisioner AWS access group: team and flattened access-group
members become direct members of javabin-developer@ (Google SCIM doesn't expand
nested groups), the missing-member reasons, and the scheduled retry.

Run: python3 -m unittest discover -s terraform/lambda-src/tests
"""

import importlib.util
import json
import os
import sys
import types
import unittest
from unittest import mock

SRC = os.path.join(os.path.dirname(__file__), "..")
DEV = "javabin-developer@java.no"


def load():
    os.environ.setdefault("MEMBERSHIPS_TABLE", "javabin-auth-memberships")
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda svc, **kw: mock.MagicMock()
    sys.modules["boto3"] = boto3
    sys.path.insert(0, SRC)
    sys.path.insert(0, os.path.join(SRC, "team_provisioner"))
    spec = importlib.util.spec_from_file_location(
        "team_provisioner_handler_aws_access", os.path.join(SRC, "team_provisioner", "handler.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.time.sleep = lambda s: None
    mod.IDENTITY_STORE_ID = "d-1"
    mod._get_google_access_token = lambda: "tok"
    mod.slack_posts = []
    mod.post_to_slack = lambda url, payload: mod.slack_posts.append(payload)
    mod.get_webhook_url = lambda ssm, param: "https://hooks"
    return mod


class FakeDirectory:
    """Stands in for _google_api: groups (email -> set of members) and users."""

    def __init__(self, groups, users=None):
        self.groups = {g: set(m) for g, m in groups.items()}
        self.users = users or {}  # email -> user resource
        self.calls = []

    def __call__(self, method, path, token, body=None):
        self.calls.append((method, path, body))
        parts = path.split("?")[0].strip("/").split("/")
        if parts[0] == "users":
            return self.users.get(parts[1].replace("%40", "@"))
        group = parts[1].replace("%40", "@")
        if len(parts) == 2:
            return {"email": group} if method == "GET" and group in self.groups else None
        if method == "GET":
            return {"members": [{"email": e} for e in sorted(self.groups.get(group, ()))]}
        if method == "POST":
            if body["email"] not in self.users:
                return None  # Google 404s an unknown member
            self.groups.setdefault(group, set()).add(body["email"])
            return {"email": body["email"]}
        return {}

    def writes(self, method):
        return [(p, b) for m, p, b in self.calls if m == method and "/members" in p]


def recorded_table(h, rows):
    h.dynamodb_client = mock.MagicMock()
    h.dynamodb_client.scan.return_value = {"Items": [
        {"pk": {"S": f"group#{name}"}, "members": {"L": [{"S": e} for e in emails]}}
        for name, emails in rows.items()]}


def active(*emails):
    return {e: {"primaryEmail": e, "suspended": False} for e in emails}


class TeamAccessGroupTest(unittest.TestCase):
    def setUp(self):
        self.h = load()

    def test_team_members_become_direct_members(self):
        d = FakeDirectory({DEV: {"alexander.amiri@java.no", "old.timer@java.no"}},
                          active("alexander.amiri@java.no", "hakon.vadstein@java.no"))
        self.h._google_api = d
        recorded_table(self.h, {"kodesmia": ["alexander.amiri@java.no"], "helter": ["old.timer@java.no"]})

        result = self.h.sync_aws_access_group([{"name": "javazoneweb", "members": [
            {"google": "hakon.vadstein", "github": "hakonv"},
            {"email": "someone@gmail.com"},
        ]}])

        self.assertEqual(result["added_members"], ["hakon.vadstein@java.no"])
        self.assertEqual(d.writes("POST")[0][1], {"email": "hakon.vadstein@java.no", "role": "MEMBER"})
        self.assertEqual(result["skipped_external"], ["someone@gmail.com"])
        # Additive: old.timer stays, and is reported (not in kodesmia or a team)
        self.assertEqual(d.writes("DELETE"), [])
        self.assertEqual(result["preserved"], ["old.timer@java.no"])
        self.assertEqual(result["member_count"], 3)

    def test_existing_members_get_no_write(self):
        d = FakeDirectory({DEV: {"ada@java.no"}}, active("ada@java.no"))
        self.h._google_api = d
        recorded_table(self.h, {})
        result = self.h.sync_aws_access_group([{"name": "x", "members": ["ada"]}])
        self.assertEqual(d.writes("POST"), [])
        self.assertEqual(result["added"], 0)
        self.assertNotIn("preserved", result)

    def test_unknown_google_user_is_reported_not_raised(self):
        self.h._google_api = FakeDirectory({DEV: set()})
        recorded_table(self.h, {})
        result = self.h.sync_aws_access_group([{"name": "x", "members": ["ghost"]}])
        self.assertEqual(result["failed"], [{"email": "ghost@java.no", "error": "not in Google Workspace"}])
        self.assertEqual(result["added"], 0)

    def test_without_teams_uses_every_recorded_team(self):
        d = FakeDirectory({DEV: set()}, active("a@java.no", "b@java.no", "k@java.no"))
        self.h._google_api = d
        recorded_table(self.h, {"team-one": ["a@java.no"], "team-two": ["b@java.no"], "kodesmia": ["k@java.no"]})
        result = self.h.sync_aws_access_group(None)
        self.assertEqual(result["added_members"], ["a@java.no", "b@java.no"])

    def test_team_sync_adds_members_and_explains_who_is_missing(self):
        h = self.h
        d = FakeDirectory({DEV: set(), "drift@java.no": set(), "styret@java.no": set()},
                          active("hakon.vadstein@java.no"))
        h._google_api = d
        recorded_table(h, {})
        team = {"name": "javazoneweb", "description": "d", "members": [{"google": "hakon.vadstein"}]}
        ic = {"synced": True, "group": "team-javazoneweb", "not_in_identity_center": ["hakon.vadstein@java.no"]}
        with mock.patch.multiple(h, sync_google_group=lambda t: {"synced": True},
                                 sync_github_team=lambda t: {"synced": True},
                                 sync_budget=lambda t: {"synced": True},
                                 sync_cognito_group=lambda t: {"synced": True},
                                 sync_identity_center_group=lambda t: ic):
            body = json.loads(h.handler({"teams": [team]}, None)["body"])

        self.assertEqual(body["aws_access_group"]["added_members"], ["hakon.vadstein@java.no"])
        self.assertEqual(body["not_in_identity_center"],
                         {"hakon.vadstein@java.no": f"pending SCIM: just added to {DEV}"})
        self.assertEqual(body["provisioned"], ["javazoneweb"])
        text = json.dumps(h.slack_posts[0])
        self.assertIn(f"hakon.vadstein@java.no (team-javazoneweb): pending SCIM: just added to {DEV}", text)
        self.assertIn(DEV, text)


class FlattenAccessGroupTest(unittest.TestCase):
    def setUp(self):
        self.h = load()
        recorded_table(self.h, {"team-javazoneweb": ["hakon.vadstein@java.no"]})

    def test_flatten_adds_individual_members(self):
        d = FakeDirectory({DEV: {"kodesmia@java.no", "hakon.vadstein@java.no", "manual@java.no"}},
                          active("ada@java.no", "bob@java.no"))
        self.h._google_api = d
        ag = {"name": "javabin-developer", "google": DEV, "groups": ["kodesmia", "nope"], "flatten": True}
        members = {"kodesmia": ["ada@java.no", "Bob@java.no", "personal@gmail.com"]}

        name, result = self.h._sync_one_access_group(ag, {"kodesmia": "kodesmia@java.no"}, "tok", members)

        self.assertEqual(name, "javabin-developer")
        self.assertEqual(result["added_members"], ["ada@java.no", "bob@java.no"])
        self.assertEqual(result["skipped_external"], ["personal@gmail.com"])
        self.assertNotIn("kodesmia@java.no", [b["email"] for _, b in d.writes("POST")])
        self.assertEqual(d.writes("DELETE"), [])
        # Team members are accounted for; the nested group and manual adds are kept and reported
        self.assertEqual(result["preserved"], ["kodesmia@java.no", "manual@java.no"])

    def test_without_flatten_role_groups_are_nested(self):
        d = FakeDirectory({"email-access@java.no": set()}, active("kodesmia@java.no"))
        self.h._google_api = d
        ag = {"name": "email-access", "google": "email-access@java.no", "groups": ["kodesmia"]}
        _, result = self.h._sync_one_access_group(
            ag, {"kodesmia": "kodesmia@java.no"}, "tok", {"kodesmia": ["ada@java.no"]})
        self.assertEqual([b["email"] for _, b in d.writes("POST")], ["kodesmia@java.no"])
        self.assertEqual(result["total_groups"], 1)

    def test_group_sync_passes_resolved_members_and_explains_missing(self):
        h = self.h
        d = FakeDirectory({DEV: set(), "drift@java.no": set(), "styret@java.no": set(),
                           "kodesmia@java.no": set()}, active("ada@java.no"))
        h._google_api = d
        h._sync_hero_account = lambda hero, tok: {"status": "existed", "email": hero["javabin_google_email"]}
        h._sync_hero_alias = lambda hero, tok: None
        ic = {"synced": True, "group": "kodesmia", "not_in_identity_center": ["ada@java.no"]}
        h.sync_group_to_identity_center = lambda g: ic
        h.sync_group_to_cognito = lambda g: {"synced": True}
        event = {
            "groups": [{"name": "kodesmia", "google": "kodesmia@java.no", "members": ["ada@java.no"],
                        "identity_center": True}],
            "heros": [],
            "access_groups": [{"name": "javabin-developer", "google": DEV, "groups": ["kodesmia"],
                               "flatten": True}],
        }
        body = json.loads(h.handle_sync_groups_and_heros(event)["body"])
        self.assertEqual(body["results"]["_access_groups"]["javabin-developer"]["added_members"], ["ada@java.no"])
        self.assertEqual(body["results"]["not_in_identity_center"],
                         {"ada@java.no": f"pending SCIM: just added to {DEV}"})


class IdentityCenterReasonsTest(unittest.TestCase):
    def test_reasons(self):
        h = load()
        users = active("new@java.no", "drift@java.no", "nobody@java.no")
        users["gone@java.no"] = {"primaryEmail": "gone@java.no", "suspended": True}
        h._google_api = FakeDirectory({DEV: {"new@java.no"}, "drift@java.no": {"drift@java.no"},
                                       "styret@java.no": set()}, users)
        reasons = h._identity_center_reasons(
            ["me@gmail.com", "ghost@java.no", "gone@java.no", "new@java.no", "drift@java.no",
             "nobody@java.no"], just_added={"new@java.no"})
        self.assertEqual(reasons, {
            "me@gmail.com": "can't be provisioned: personal address, not a java.no account",
            "ghost@java.no": "can't be provisioned: not in Google Workspace",
            "gone@java.no": "can't be provisioned: suspended in Google",
            "new@java.no": f"pending SCIM: just added to {DEV}",
            "drift@java.no": "pending SCIM: already a direct member of a provisioned group",
            "nobody@java.no": f"not a direct member of {DEV}",
        })

    def test_classification_failure_does_not_fail_sync(self):
        h = load()

        def boom():
            raise RuntimeError("no token")
        h._get_google_access_token = boom
        results = {"x": {"identity_center": {"not_in_identity_center": ["a@java.no"]}}}
        self.assertEqual(h._explain_not_in_identity_center(results), {})


def identity_store(users, groups):
    """users: email -> id; groups: name -> (id, [member user ids])."""
    store = mock.MagicMock()
    store.exceptions.ConflictException = type("Conflict", (Exception,), {})
    store.list_groups.return_value = {"Groups": [
        {"DisplayName": n, "GroupId": gid} for n, (gid, _) in groups.items()]}
    by_id = {gid: members for gid, members in groups.values()}
    store.list_group_memberships.side_effect = lambda IdentityStoreId, GroupId: {
        "GroupMemberships": [{"MemberId": {"UserId": u}} for u in by_id[GroupId]]}
    store.list_users.side_effect = lambda IdentityStoreId, Filters: {
        "Users": [{"UserId": users[Filters[0]["AttributeValue"]]}] if Filters[0]["AttributeValue"] in users else []}
    return store


class SyncAccessMembersTest(unittest.TestCase):
    def setUp(self):
        self.h = load()
        recorded_table(self.h, {"team-javazoneweb": ["hakon.vadstein@java.no"],
                                "kodesmia": ["ada@java.no", "pending@java.no"],
                                "helter": ["ada@java.no"]})

    def test_retries_members_scim_has_since_created(self):
        h = self.h
        d = FakeDirectory({DEV: {"hakon.vadstein@java.no"}, "drift@java.no": set(), "styret@java.no": set()},
                          active("hakon.vadstein@java.no", "pending@java.no"))
        h._google_api = d
        h.identitystore_client = identity_store(
            {"hakon.vadstein@java.no": "u-h", "ada@java.no": "u-a"},
            {"team-javazoneweb": ("g-t", []), "kodesmia": ("g-k", ["u-a"]), "drift": ("g-d", [])})

        body = json.loads(h.handler({"action": "sync_access_members"}, None)["body"])

        h.identitystore_client.create_group_membership.assert_called_once_with(
            IdentityStoreId="d-1", GroupId="g-t", MemberId={"UserId": "u-h"})
        self.assertEqual(body["identity_center"]["team-javazoneweb"]["added"], ["hakon.vadstein@java.no"])
        self.assertEqual(body["identity_center"]["kodesmia"],
                         {"added": [], "not_in_identity_center": ["pending@java.no"]})
        self.assertNotIn("helter", body["identity_center"])  # no Identity Center group
        self.assertEqual(d.writes("POST"), [])
        self.assertEqual(len(h.slack_posts), 1)
        self.assertIn("hakon.vadstein@java.no", json.dumps(h.slack_posts[0]))

    def test_quiet_when_nothing_changed(self):
        h = self.h
        h._google_api = FakeDirectory({DEV: {"hakon.vadstein@java.no"}})
        h.identitystore_client = identity_store(
            {"hakon.vadstein@java.no": "u-h"}, {"team-javazoneweb": ("g-t", ["u-h"])})
        h.handler({"action": "sync_access_members"}, None)
        h.identitystore_client.create_group_membership.assert_not_called()
        self.assertEqual(h.slack_posts, [])

    def test_adds_recorded_team_members_to_access_group(self):
        h = self.h
        d = FakeDirectory({DEV: set(), "drift@java.no": set(), "styret@java.no": set()},
                          active("hakon.vadstein@java.no"))
        h._google_api = d
        h.identitystore_client = identity_store({}, {"team-javazoneweb": ("g-t", [])})
        body = json.loads(h.handler({"action": "sync_access_members"}, None)["body"])
        self.assertEqual(body["aws_access_group"]["added_members"], ["hakon.vadstein@java.no"])
        self.assertEqual(body["not_in_identity_center"],
                         {"hakon.vadstein@java.no": f"pending SCIM: just added to {DEV}"})
        self.assertEqual(len(h.slack_posts), 1)


if __name__ == "__main__":
    unittest.main()
