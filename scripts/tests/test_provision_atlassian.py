"""Tests for provision-atlassian.py — run: python3 -m unittest discover -s scripts/tests"""

import importlib.util
import os
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("provision_atlassian", os.path.join(HERE, "..", "provision-atlassian.py"))
pa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pa)

MEMBERS = {"members": [
    {"personal_email": "anne@gmail.com", "javabin_google_email": "Anne.A@java.no", "memberships": ["helter", "styret"]},
    {"personal_email": "bob@gmail.com", "javabin_google_email": "bob.b@java.no", "memberships": ["helter", "drift"]},
    {"personal_email": "carl@gmail.com", "javabin_google_email": "", "memberships": ["helter"]},
]}


class ResolveTest(unittest.TestCase):
    def test_unions_source_groups_into_java_no_emails(self):
        atl = {"groups": [
            {"name": "confluence-users", "description": "All heroes", "from": ["helter"]},
            {"name": "confluence-admins-javabin", "from": ["drift", "styret"]},
            {"name": "empty", "from": ["kodesmia"]},
        ]}
        got = {g["name"]: g for g in pa.resolve_atlassian_groups(atl, MEMBERS)}
        self.assertEqual(got["confluence-users"]["members"], ["anne.a@java.no", "bob.b@java.no"])
        self.assertEqual(got["confluence-users"]["description"], "All heroes")
        self.assertEqual(got["confluence-admins-javabin"]["members"], ["anne.a@java.no", "bob.b@java.no"])
        self.assertEqual(got["empty"]["members"], [])

    def test_personal_emails_never_reach_the_payload(self):
        atl = {"groups": [{"name": "confluence-users", "from": ["helter"]}]}
        emails = pa.resolve_atlassian_groups(atl, MEMBERS)[0]["members"]
        self.assertTrue(all(e.endswith("@java.no") for e in emails))


if __name__ == "__main__":
    unittest.main()
