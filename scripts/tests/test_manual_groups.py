"""Tests for `manual: true` groups in the registry sync scripts.

A manual group (javazone@java.no) is kept by hand in Google Admin: no script may
add members to it or plan removals from it, and the hero sheet maps nothing into it.

Run: python3 -m unittest discover -s scripts/tests
"""

import contextlib
import importlib.util
import io
import os
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(HERE, "..", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


GROUPS = {"groups": [
    {"name": "helter", "google": "helter@java.no", "cognito": True},
    {"name": "javazone", "google": "javazone@java.no", "manual": True},
]}

# A stale members.yaml that still lists the manual group must not leak into it.
MEMBERS = {"members": [
    {"firstname": "Ola", "lastname": "Nordmann", "personal_email": "ola@example.com",
     "javabin_google_email": "ola.nordmann@java.no", "memberships": ["helter", "javazone"]},
    {"firstname": "Kari", "lastname": "Nordmann", "personal_email": "kari@example.com",
     "javabin_google_email": "kari.nordmann@java.no", "memberships": ["helter"]},
]}


class ProvisionGroupsTest(unittest.TestCase):
    def setUp(self):
        try:
            self.pg = load("provision_groups", "provision-groups.py")
        except ModuleNotFoundError as e:  # PyYAML is on the CI runner
            self.skipTest(str(e))

    def test_manual_group_resolves_to_no_members(self):
        got = {g["name"]: g for g in self.pg.resolve_memberships(GROUPS, MEMBERS)}
        self.assertEqual(got["helter"]["members"], ["ola.nordmann@java.no", "kari.nordmann@java.no"])
        self.assertEqual(got["javazone"]["members"], [])
        self.assertIs(got["javazone"]["manual"], True)  # the Lambda skips it too


class ReconcileGroupsTest(unittest.TestCase):
    def setUp(self):
        try:
            self.rg = load("reconcile_groups", "reconcile-groups.py")
        except ModuleNotFoundError as e:
            self.skipTest(str(e))

    def test_no_removals_planned_for_manual_group(self):
        current = {
            "helter@java.no": [{"email": "ola.nordmann@java.no", "type": "USER"},
                               {"email": "kari.nordmann@java.no", "type": "USER"}],
            "javazone@java.no": [{"email": "kari.nordmann@java.no", "type": "USER"},
                                 {"email": "jz-koordineringsgruppen@java.no", "type": "GROUP"}],
        }
        users = [{"primaryEmail": "ola.nordmann@java.no"}, {"primaryEmail": "kari.nordmann@java.no"}]
        with mock.patch.object(self.rg, "list_workspace_users", return_value=users), \
                mock.patch.object(self.rg, "list_group_members", side_effect=lambda t, g: current[g]) as lgm, \
                contextlib.redirect_stdout(io.StringIO()):
            plan = self.rg.generate_plan("token", MEMBERS, GROUPS, {})
        self.assertEqual(plan["actions"], [])
        self.assertNotIn("javazone@java.no", [c.args[1] for c in lgm.call_args_list])


class SyncMembersTest(unittest.TestCase):
    def setUp(self):
        self.sm = load("sync_members", "sync-members.py")

    def test_javazone_choice_maps_to_no_group_without_warning(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            got = self.sm.map_groups("JavaZone, Kodesmia")
        self.assertEqual(got, ["helter", "kodesmia"])
        self.assertEqual(err.getvalue(), "")

    def test_unknown_choice_still_warns(self):
        err = io.StringIO()
        with contextlib.redirect_stderr(err):
            self.assertEqual(self.sm.map_groups("Nope"), ["helter"])
        self.assertIn("unknown group 'Nope'", err.getvalue())


class SortMembersTest(unittest.TestCase):
    def setUp(self):
        try:
            self.sort = load("sort_members", "sort-members.py")
        except ModuleNotFoundError as e:
            self.skipTest(str(e))

    def test_javazone_is_not_a_section(self):
        self.assertNotIn("javazone", self.sort.GROUP_PRIORITY)
        self.assertEqual(self.sort.primary_section({"memberships": ["helter"]}), "other")


if __name__ == "__main__":
    unittest.main()
