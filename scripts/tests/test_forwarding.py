"""Forwarding to personal email is opt-in (`forward_to_personal: true` in members.yaml).

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


def member(first, forward=None, **extra):
    m = {"firstname": first, "lastname": "Nordmann", "personal_email": f"{first.lower()}@gmail.com",
         "javabin_google_email": f"{first.lower()}.nordmann@java.no", "memberships": ["helter"], **extra}
    if forward is not None:
        m["forward_to_personal"] = forward
    return m


MEMBERS = {"members": [member("Ola", True), member("Kari"), member("Per", False), member("Anne", "yes")]}


class AddressMapTest(unittest.TestCase):
    def setUp(self):
        self.gam = load("gmail_address_map", "gmail-address-map.py")

    def test_only_opted_in_members(self):
        self.assertEqual(self.gam.address_map(MEMBERS), [("ola.nordmann@java.no", "ola@gmail.com")])

    def test_needs_both_addresses(self):
        data = {"members": [member("Ola", True, personal_email=""), member("Kari", True, javabin_google_email=" ")]}
        self.assertEqual(self.gam.address_map(data), [])


class ProvisionGroupsForwardingTest(unittest.TestCase):
    def setUp(self):
        try:
            self.pg = load("provision_groups_fwd", "provision-groups.py")
        except ModuleNotFoundError as e:  # PyYAML is on the CI runner
            self.skipTest(str(e))

    def test_changes(self):
        now = {"members": [member("Ola"), member("Kari", True), member("Per", False)]}
        self.assertEqual(self.pg.forwarding_changes(MEMBERS, now),
                         (["kari.nordmann@java.no"], ["ola.nordmann@java.no"]))
        self.assertEqual(self.pg.forwarding_changes(MEMBERS, MEMBERS), ([], []))

    def test_reminder_names_java_no_addresses_only(self):
        out = io.StringIO()
        with mock.patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.example/x"}), \
                mock.patch.object(self.pg.urllib.request, "urlopen") as urlopen, \
                contextlib.redirect_stdout(out):
            self.pg.post_forwarding_reminder(["kari.nordmann@java.no"], ["ola.nordmann@java.no"])
        sent = urlopen.call_args.args[0].data.decode()
        self.assertIn("kari.nordmann@java.no", sent)
        self.assertIn("gmail-address-map.py", sent)
        self.assertNotIn("@gmail.com", sent)

    def test_member_details_carry_no_forwarding(self):
        # The Lambda never sets up forwarding; nothing in the payload asks for it.
        details = self.pg.build_member_details(MEMBERS)
        self.assertTrue(all("forward_to_personal" not in d for d in details))


class DiffGroupsForwardingTest(unittest.TestCase):
    def setUp(self):
        try:
            self.dg = load("diff_groups_fwd", "diff-groups.py")
        except ModuleNotFoundError as e:
            self.skipTest(str(e))

    def test_flag_change_shows_in_plan(self):
        _, _, changed = self.dg.diff_members({"members": [member("Ola")]}, {"members": [member("Ola", True)]})
        self.assertEqual(changed[0]["diffs"], ["`forward_to_personal`: '' → True"])


class SerializersKeepFlagTest(unittest.TestCase):
    """The sort bot and the sheet sync rewrite members.yaml; they must keep the opt-in."""

    def test_sort_members_keeps_flag(self):
        try:
            sort = load("sort_members_fwd", "sort-members.py")
        except ModuleNotFoundError as e:
            self.skipTest(str(e))
        out = sort.format_yaml(sort.sort_members(MEMBERS["members"]))
        self.assertEqual(out.count("forward_to_personal: true"), 1)
        ola = out[out.index("firstname: Ola"):]
        self.assertLess(ola.index("forward_to_personal: true"), ola.index("memberships:"))

    def test_sync_members_preserves_flag_from_registry(self):
        sm = load("sync_members_fwd", "sync-members.py")
        existing = {m["personal_email"]: dict(m) for m in MEMBERS["members"]}
        fresh = [{**member("Ola"), "memberships": ["helter", "kodesmia"]}, member("Kari")]
        merged, _, _ = sm.merge_heroes(existing, fresh)
        by_first = {m["firstname"]: m for m in merged}
        self.assertIs(by_first["Ola"].get("forward_to_personal"), True)
        self.assertNotIn("forward_to_personal", by_first["Kari"])
        self.assertEqual(sm.format_yaml(merged).count("forward_to_personal: true"), 1)


if __name__ == "__main__":
    unittest.main()
