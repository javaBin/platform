"""Tests for team_provisioner AWS access: post-apply team access sync, members
missing from Identity Center, GitHub team roles.

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


def load():
    os.environ.setdefault("MEMBERSHIPS_TABLE", "javabin-auth-memberships")
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda svc, **kw: mock.MagicMock()
    sys.modules["boto3"] = boto3
    sys.path.insert(0, SRC)
    sys.path.insert(0, os.path.join(SRC, "team_provisioner"))
    spec = importlib.util.spec_from_file_location(
        "team_provisioner_handler_access", os.path.join(SRC, "team_provisioner", "handler.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.time.sleep = lambda s: None
    mod.IDENTITY_STORE_ID = "d-1"
    mod.SSO_INSTANCE_ARN = "arn:sso"
    mod.ACCOUNT_ID = "123"
    mod.slack_posts = []
    mod.post_to_slack = lambda url, payload: mod.slack_posts.append(payload)
    mod.get_webhook_url = lambda ssm, param: "https://hooks"
    return mod


def identity_store(users, members=()):
    """Identity store with `users` (email -> user id) and the group holding `members`."""
    store = mock.MagicMock()
    store.exceptions.ConflictException = type("Conflict", (Exception,), {})
    store.list_groups.return_value = {"Groups": [{"GroupId": "g1"}]}
    store.list_group_memberships.return_value = {
        "GroupMemberships": [{"MemberId": {"UserId": users[e]}, "MembershipId": f"m-{e}"} for e in members]}
    by_id = {uid: email for email, uid in users.items()}
    store.describe_user.side_effect = lambda IdentityStoreId, UserId: {"Emails": [{"Value": by_id[UserId]}]}

    def list_users(IdentityStoreId, Filters):
        email = Filters[0]["AttributeValue"]
        return {"Users": [{"UserId": users[email]}] if email in users else []}
    store.list_users.side_effect = list_users
    return store


def sso_admin(assigned_groups=(), list_fails=False, provision_status="SUCCEEDED"):
    """SSO admin client: `assigned_groups` already hold the permission set.
    Like AWS, creating an existing assignment succeeds without a conflict."""
    sso = mock.MagicMock()
    sso.exceptions.ConflictException = type("Conflict", (Exception,), {})
    sso.exceptions.ResourceNotFoundException = type("NotFound", (Exception,), {})
    if list_fails:
        sso.list_account_assignments.side_effect = Exception("AccessDenied")
    else:
        sso.list_account_assignments.return_value = {"AccountAssignments": [
            {"PrincipalType": "GROUP", "PrincipalId": g} for g in assigned_groups]}
    sso.create_account_assignment.return_value = {"AccountAssignmentCreationStatus": {"RequestId": "a1"}}
    sso.describe_account_assignment_creation_status.return_value = {
        "AccountAssignmentCreationStatus": {"Status": "SUCCEEDED"}}
    sso.provision_permission_set.return_value = {"PermissionSetProvisioningStatus": {"RequestId": "p1"}}
    sso.describe_permission_set_provisioning_status.return_value = {
        "PermissionSetProvisioningStatus": {"Status": provision_status, "FailureReason": "boom"}}
    return sso


class SyncTeamAccessTest(unittest.TestCase):
    def setUp(self):
        self.h = load()
        self.h.identitystore_client = mock.MagicMock()
        self.h.identitystore_client.list_groups.side_effect = [
            {"Groups": [{"DisplayName": "team-cupcake", "GroupId": "g-c"},
                        {"DisplayName": "styret", "GroupId": "g-s"}], "NextToken": "t"},
            {"Groups": [{"DisplayName": "team-platform", "GroupId": "g-p"},
                        {"DisplayName": "team-Bad-Name", "GroupId": "g-x"}]},
        ]
        self.h._resolve_permission_set_arn = lambda name: f"arn:ps/{name}"
        self.h._guardrails_state = lambda name: "ok" if name == "javabin-team-cupcake-guardrails" else "pending: x"

    def test_grants_access_to_teams_whose_guardrails_exist(self):
        self.h.sso_client = sso_admin()
        body = json.loads(self.h.handler({"action": "sync_team_access"}, None)["body"])

        create = self.h.sso_client.create_account_assignment.call_args.kwargs
        self.assertEqual((create["PermissionSetArn"], create["PrincipalId"]), ("arn:ps/team-cupcake", "g-c"))
        self.assertEqual(self.h.sso_client.create_account_assignment.call_count, 1)
        self.assertEqual(body["synced"], ["cupcake", "platform"])
        self.assertTrue(body["results"]["platform"]["guardrails"].startswith("pending"))
        # A newly made assignment is announced, the pending team is not
        self.assertEqual(len(self.h.slack_posts), 1)
        text = json.dumps(self.h.slack_posts[0])
        self.assertIn("cupcake", text)
        self.assertNotIn("platform", text)

    def test_existing_assignment_stays_quiet(self):
        self.h.sso_client = sso_admin(assigned_groups=["g-other", "g-c"])
        body = json.loads(self.h.handler({"action": "sync_team_access"}, None)["body"])
        self.h.sso_client.create_account_assignment.assert_not_called()
        self.h.sso_client.provision_permission_set.assert_called_once()  # policy changes still roll out
        self.assertTrue(body["results"]["cupcake"]["already_existed"])
        self.assertEqual(self.h.slack_posts, [])

    def test_unknown_assignment_state_stays_quiet(self):
        self.h.sso_client = sso_admin(list_fails=True)
        self.h.handler({"action": "sync_team_access"}, None)
        self.h.sso_client.create_account_assignment.assert_called_once()
        self.assertEqual(self.h.slack_posts, [])

    def test_failed_provisioning_is_announced(self):
        self.h.sso_client = sso_admin(assigned_groups=["g-c"], provision_status="FAILED")
        self.h.handler({"action": "sync_team_access"}, None)
        self.assertIn("provisioning failed: boom", json.dumps(self.h.slack_posts[0]))

    def test_teams_filter(self):
        self.h.sso_client = sso_admin()
        body = json.loads(self.h.handler({"action": "sync_team_access", "teams": ["platform"]}, None)["body"])
        self.assertEqual(body["synced"], ["platform"])
        self.h.sso_client.create_account_assignment.assert_not_called()


class NotInIdentityCenterTest(unittest.TestCase):
    def test_team_sync_reports_members_without_identity_center_user(self):
        h = load()
        h.SSO_INSTANCE_ARN = ""  # no permission set work
        h.identitystore_client = identity_store({"ada@java.no": "u1"})
        team = {"name": "cupcake", "description": "d", "members": ["ada", "hakon"]}

        result = h.sync_identity_center_group(team)

        self.assertEqual(result["not_in_identity_center"], ["hakon@java.no"])
        self.assertEqual(result["member_count"], 1)
        h.identitystore_client.create_group_membership.assert_called_once()

    def test_group_sync_reports_members_without_identity_center_user(self):
        h = load()
        h.SSO_INSTANCE_ARN = ""
        h.identitystore_client = identity_store({"ada@java.no": "u1"}, members=["ada@java.no"])
        h._get_google_group_members = lambda email: ["ada@java.no", "bob@java.no"]

        result = h.sync_group_to_identity_center(
            {"name": "styret", "google": "styret@java.no", "identity_center": True})

        self.assertEqual(result["not_in_identity_center"], ["bob@java.no"])
        self.assertEqual(result["member_count"], 1)

    def test_everyone_present_reports_nothing(self):
        h = load()
        h.SSO_INSTANCE_ARN = ""
        h.identitystore_client = identity_store({"ada@java.no": "u1"})
        result = h.sync_identity_center_group({"name": "cupcake", "description": "d", "members": ["ada"]})
        self.assertNotIn("not_in_identity_center", result)

    def test_slack_summary_lists_missing_members_with_fix(self):
        h = load()
        results = {
            "javazoneweb": {"identity_center": {"synced": True, "group": "team-javazoneweb", "member_count": 2,
                                                "not_in_identity_center": ["hakon@java.no"]}},
            "styret": {"identity_center": {"synced": True, "group": "styret",
                                           "not_in_identity_center": ["hakon@java.no", "bob@java.no"]}},
        }
        h.notify_slack(results)

        blocks = h.slack_posts[0]["blocks"]
        texts = [b.get("text", {}).get("text", "") for b in blocks]
        self.assertTrue(any("no AWS access, not in Identity Center: hakon@java.no" in t for t in texts))
        summary = next(t for t in texts if "member(s) have no AWS access" in t)
        self.assertIn("*2 member(s)", summary)
        self.assertIn("hakon@java.no (styret, team-javazoneweb)", summary)
        self.assertIn("direct members of", summary)

    def test_slack_summary_omitted_when_nobody_missing(self):
        h = load()
        h.notify_slack({"cupcake": {"identity_center": {"synced": True, "member_count": 1}}})
        self.assertFalse(any("no AWS access" in json.dumps(b) for b in h.slack_posts[0]["blocks"]))

    def test_pending_guardrails_shown_in_team_line(self):
        h = load()
        line = h._result_line("identity_center", {
            "synced": True, "member_count": 1,
            "permission_set": {"permission_set": "javabin-team-x", "guardrails": "pending: policy not created yet"}})
        self.assertIn("AWS access pending", line)


class GitHubRoleTest(unittest.TestCase):
    def _sync(self, members, current=None):
        h = load()
        h._get_github_installation_token = lambda: "tok"
        calls = []
        current = current or {}

        def api(method, path, token, body=None):
            calls.append((method, path, body))
            if method == "GET" and "/members?role=" in path:
                role = path.split("role=")[1].split("&")[0]
                if "page=1" in path:
                    return [{"login": l} for l, r in current.items() if r == role]
                return []
            return {}
        h._github_api = api
        h.sync_github_team({"name": "cupcake", "description": "d", "members": members})
        return {p.rsplit("/", 1)[1]: b["role"] for m, p, b in calls if m == "PUT"}

    def test_members_default_to_maintainer(self):
        self.assertEqual(self._sync([{"google": "ada", "github": "Ada"}]), {"ada": "maintainer"})

    def test_explicit_member_role_is_kept(self):
        roles = self._sync([{"google": "ada", "github": "ada", "role": "member"},
                            {"google": "bob", "github": "bob", "role": "maintainer"}])
        self.assertEqual(roles, {"ada": "member", "bob": "maintainer"})

    def test_existing_member_without_role_is_promoted(self):
        self.assertEqual(self._sync([{"google": "ada", "github": "ada"}], current={"ada": "member"}),
                         {"ada": "maintainer"})


if __name__ == "__main__":
    unittest.main()
