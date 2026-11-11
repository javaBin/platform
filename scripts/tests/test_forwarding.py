"""Forwarding to personal email is opt-in (`forward_to_personal: true` in members.yaml).

Seams: the scripts' public functions and `provision-groups.py main()`, with the Lambda
invocation and the Slack webhook (urllib) patched out.

Run: python3 -m unittest discover -s scripts/tests
"""

import contextlib
import importlib.util
import io
import json
import os
import sys
import tempfile
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
                         (["kari.nordmann@java.no"], ["ola.nordmann@java.no"], []))
        self.assertEqual(self.pg.forwarding_changes(MEMBERS, MEMBERS), ([], [], []))

    def test_personal_email_change_of_opted_in_member(self):
        now = {"members": [member("Ola", True, personal_email="ola@new.example")]}
        self.assertEqual(self.pg.forwarding_changes(MEMBERS, now), ([], [], ["ola.nordmann@java.no"]))

    def run_main(self, prev, curr):
        """Run main() on YAML files; returns the Slack payloads it posted."""
        import yaml
        with tempfile.TemporaryDirectory() as d:
            paths = {}
            for name, data in {"groups": {"groups": []}, "prev": prev, "curr": curr}.items():
                paths[name] = os.path.join(d, f"{name}.yaml")
                with open(paths[name], "w") as f:
                    yaml.safe_dump(data, f)
            argv = ["provision-groups.py", paths["groups"], paths["curr"], "--prev-members", paths["prev"]]
            with mock.patch.object(sys, "argv", argv), \
                    mock.patch.dict(os.environ, {"SLACK_WEBHOOK_URL": "https://hooks.example/x"}), \
                    mock.patch.object(self.pg, "invoke_lambda"), \
                    mock.patch.object(self.pg.urllib.request, "urlopen") as urlopen, \
                    contextlib.redirect_stdout(io.StringIO()):
                self.pg.main()
        return [json.loads(c.args[0].data)["text"] for c in urlopen.call_args_list]

    def test_main_posts_reminder_without_personal_emails(self):
        now = {"members": [member("Ola", True, personal_email="ola@new.example"), member("Kari", True),
                           member("Per", False), member("Anne", "yes")]}
        texts = [t for t in self.run_main(MEMBERS, now) if "Forwarding to personal email" in t]
        self.assertEqual(len(texts), 1)
        self.assertIn("*Turned on:* kari.nordmann@java.no", texts[0])
        self.assertIn("*Personal email changed:* ola.nordmann@java.no", texts[0])
        for m in MEMBERS["members"] + now["members"]:
            self.assertNotIn(m["personal_email"], texts[0])

    def test_main_posts_nothing_when_opt_ins_unchanged(self):
        self.assertEqual(self.run_main(MEMBERS, MEMBERS), [])

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
        self.assertEqual(len(changed[0]["diffs"]), 1)
        self.assertIn("`forward_to_personal`: False → True", changed[0]["diffs"][0])
        # an explicit false is the same as no flag
        self.assertEqual(self.dg.diff_members({"members": [member("Ola", False)]}, {"members": [member("Ola")]})[2], [])

    def test_new_member_opt_in_shows_in_plan(self):
        added, _, _ = self.dg.diff_members({"members": []}, {"members": [member("Ola", True)]})
        md = self.dg.format_markdown(added, [], [], [], [], [], [], [])
        self.assertIn("forward_to_personal", md)


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
