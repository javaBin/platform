"""The provisioner never sets up Gmail forwarding; opt-in forwarding is a Gmail routing rule.

Run: python3 -m unittest discover -s terraform/lambda-src/tests
"""

import importlib.util
import os
import sys
import types
import unittest
from unittest import mock

SRC = os.path.join(os.path.dirname(__file__), "..")


def load():
    os.environ.setdefault("MEMBERSHIPS_TABLE", "javabin-auth-memberships")
    os.environ.setdefault("COGNITO_INTERNAL_POOL_ID", "pool")
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda svc, **kw: mock.MagicMock()
    sys.modules["boto3"] = boto3
    sys.path.insert(0, SRC)
    sys.path.insert(0, os.path.join(SRC, "team_provisioner"))
    spec = importlib.util.spec_from_file_location(
        "team_provisioner_handler_fwd", os.path.join(SRC, "team_provisioner", "handler.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class NoForwardingTest(unittest.TestCase):
    HERO = {"javabin_google_email": "ola.nordmann@java.no", "firstname": "Ola", "lastname": "Nordmann",
            "personal_email": "ola@gmail.com", "alias": ""}

    def sync(self, h, create_result):
        calls = []

        def google_api(method, path, token, body=None):
            calls.append((method, path))
            return None if method == "GET" else create_result
        with mock.patch.object(h, "_google_api", side_effect=google_api), \
                mock.patch.object(h, "_generate_password_set_url", return_value="https://x"), \
                mock.patch.object(h, "_send_welcome_email") as welcome, \
                mock.patch.object(h.urllib.request, "urlopen") as urlopen:
            result = h._sync_hero_account(self.HERO, "tok")
        return result, calls, welcome, urlopen

    def test_new_account_gets_welcome_mail_but_no_forwarding(self):
        h = load()
        result, calls, welcome, urlopen = self.sync(h, {"primaryEmail": "ola.nordmann@java.no"})
        self.assertEqual(result["status"], "created")
        welcome.assert_called_once()
        urlopen.assert_not_called()  # no Gmail settings call
        self.assertEqual([m for m, _ in calls], ["GET", "POST"])

    def test_existing_account_conflict_sets_up_nothing(self):
        h = load()
        result, calls, welcome, urlopen = self.sync(h, {"already_exists": True})
        self.assertEqual(result["status"], "existed")
        welcome.assert_not_called()
        urlopen.assert_not_called()

    def test_no_forwarding_code_or_scope(self):
        h = load()
        self.assertFalse(hasattr(h, "_setup_email_forwarding"))
        self.assertNotIn("gmail.settings.sharing", h.GOOGLE_SCOPES)


if __name__ == "__main__":
    unittest.main()
