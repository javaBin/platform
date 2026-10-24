"""Tests for team_provisioner/atlassian.py — run: python3 -m unittest discover -s terraform/lambda-src/tests"""

import io
import json
import os
import sys
import unittest
import urllib.error

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "team_provisioner"))
import atlassian


def user(account_id, email, managed=True):
    return {"accountId": account_id, "email": email, "claimStatus": "managed" if managed else "unmanaged"}


class FakeAdmin:
    def __init__(self, users, groups, members):
        self._users = users
        self._groups = groups
        self._members = members  # group id -> [user]
        self.calls = []

    def users(self, group_id=None):
        return self._members.get(group_id, []) if group_id else self._users

    def groups(self):
        return self._groups

    def create_group(self, name, description=""):
        self.calls.append(("create", name))
        return {"id": f"new-{name}"}

    def add_member(self, group_id, account_id):
        self.calls.append(("add", group_id, account_id))

    def remove_member(self, group_id, account_id):
        self.calls.append(("remove", group_id, account_id))


ANNE = user("a1", "anne.a@java.no")
BOB = user("b1", "bob.b@java.no")
BOB_PERSONAL = user("b2", "bob@gmail.com", managed=False)


class PlanGroupTest(unittest.TestCase):
    def test_adds_registry_members_and_removes_everyone_else(self):
        accounts = {u["email"]: u for u in (ANNE, BOB, BOB_PERSONAL)}
        plan = atlassian.plan_group(
            ["Anne.A@java.no", "bob.b@java.no", "new.person@java.no"], [BOB, BOB_PERSONAL], accounts,
        )
        self.assertEqual([u["accountId"] for u in plan["add"]], ["a1"])
        self.assertEqual([u["accountId"] for u in plan["remove"]], ["b2"])
        self.assertEqual(plan["missing_account"], ["new.person@java.no"])


class SyncTest(unittest.TestCase):
    def admin(self):
        return FakeAdmin(
            users=[ANNE, BOB, BOB_PERSONAL],
            groups=[
                {"id": "g-styret", "name": "styret"},
                {"id": "g-gsuite", "name": "All users from G Suite", "externalSynced": True},
                {"id": "g-admins", "name": "org-admins"},
            ],
            members={"g-styret": [BOB_PERSONAL]},
        )

    def test_report_mode_plans_without_writing(self):
        admin = self.admin()
        results = atlassian.sync(admin, [{"name": "styret", "members": ["anne.a@java.no"]}])
        self.assertEqual(admin.calls, [])
        self.assertEqual(results["styret"]["add"], ["anne.a@java.no"])
        self.assertEqual(results["styret"]["remove"], ["bob@gmail.com (personal)"])
        self.assertFalse(results["styret"]["applied"])

    def test_apply_creates_missing_groups_and_enforces_membership(self):
        admin = self.admin()
        atlassian.sync(admin, [
            {"name": "styret", "members": ["anne.a@java.no"]},
            {"name": "kodesmia", "members": ["bob.b@java.no"]},
        ], apply=True)
        self.assertEqual(admin.calls, [
            ("add", "g-styret", "a1"),
            ("remove", "g-styret", "b2"),
            ("create", "kodesmia"),
            ("add", "new-kodesmia", "b1"),
        ])

    def test_all_java_no_accounts_adds_every_managed_java_no_account(self):
        admin = self.admin()
        admin._users = [*admin._users, user("x1", "cfo@java.no"), user("d1", "drift@java.no", managed=False),
                        {**user("s1", "gone@java.no"), "accountStatus": "inactive"}]
        results = atlassian.sync(admin, [{"name": "styret", "members": [], "all_java_no_accounts": True}])
        self.assertEqual(results["styret"]["add"], ["anne.a@java.no", "bob.b@java.no", "cfo@java.no"])
        self.assertEqual(results["styret"]["remove"], ["bob@gmail.com (personal)"])

    def test_refuses_protected_and_idp_synced_groups(self):
        admin = self.admin()
        results = atlassian.sync(admin, [
            {"name": "org-admins", "members": ["anne.a@java.no"]},
            {"name": "All users from G Suite", "members": []},
        ], apply=True)
        self.assertEqual(admin.calls, [])
        self.assertIn("protected", results["org-admins"]["error"])
        self.assertIn("identity provider", results["All users from G Suite"]["error"])

    def test_summary_has_counts_not_emails(self):
        results = atlassian.sync(self.admin(), [{"name": "styret", "members": ["anne.a@java.no"]}])
        text = atlassian.summary_text(results, apply=False)
        self.assertIn("report only", text)
        self.assertIn("`styret`: +1, -1 (1 personal)", text)
        self.assertNotIn("@", text)


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class ClientTest(unittest.TestCase):
    def test_follows_cursor_pages_and_retries_rate_limits(self):
        pages = iter([
            urllib.error.HTTPError("u", 429, "slow down", {"Retry-After": "0"}, io.BytesIO()),
            {"data": [{"directoryId": "d1"}]},
            {"data": [ANNE], "links": {"next": "c2"}},
            {"data": [BOB], "links": {"next": None}},
        ])
        urls = []

        def urlopen(req):
            urls.append(req.full_url)
            nxt = next(pages)
            if isinstance(nxt, Exception):
                raise nxt
            return Response(json.dumps(nxt).encode())

        admin = atlassian.AtlassianAdmin("org1", "key", urlopen=urlopen, sleep=lambda s: None)
        self.assertEqual([u["accountId"] for u in admin.users()], ["a1", "b1"])
        self.assertTrue(urls[0].endswith("/orgs/org1/directories"))
        self.assertIn("cursor=c2", urls[-1])

    def test_client_errors_are_raised_with_detail(self):
        def urlopen(req):
            raise urllib.error.HTTPError("u", 403, "no", {}, io.BytesIO(b"forbidden"))

        admin = atlassian.AtlassianAdmin("org1", "key", urlopen=urlopen, sleep=lambda s: None)
        with self.assertRaisesRegex(RuntimeError, "403: forbidden"):
            admin.groups()


class HandlerTest(unittest.TestCase):
    def test_sync_atlassian_action_is_report_only_by_default(self):
        from unittest import mock

        from test_team_provisioner_memberships import FakeDynamo, load

        os.environ.pop("ATLASSIAN_SYNC_APPLY", None)
        mod = load(FakeDynamo([]))
        admin = FakeAdmin(users=[ANNE], groups=[], members={})
        with mock.patch.object(mod.atlassian, "AtlassianAdmin", return_value=admin), \
                mock.patch.object(mod, "_get_ssm_param", return_value="x"), \
                mock.patch.object(mod, "post_to_slack") as slack:
            resp = mod.handler({"action": "sync_atlassian", "groups": [
                {"name": "kodesmia", "members": ["anne.a@java.no"]},
            ]}, None)
        body = json.loads(resp["body"])
        self.assertFalse(body["apply"])
        self.assertTrue(body["results"]["kodesmia"]["create"])
        self.assertEqual(admin.calls, [])
        self.assertIn("report only", slack.call_args[0][1]["text"])


if __name__ == "__main__":
    unittest.main()
