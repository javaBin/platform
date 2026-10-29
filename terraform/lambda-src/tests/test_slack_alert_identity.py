"""Tests for slack_alert GitHub OIDC attribution — run: python3 -m unittest discover -s terraform/lambda-src/tests"""

import unittest

from test_slack_alert_ecs import FakeTable, load


def ci_detail(sub):
    return {
        "userIdentity": {
            "type": "AssumedRole",
            "arn": "arn:aws:sts::111111111111:assumed-role/javabin-ci-app-broker/someone-deadbeef-42",
            "sessionContext": {"webIdFederationData": {"attributes": {"sub": sub}}},
        }
    }


class CiIdentityTests(unittest.TestCase):
    def setUp(self):
        self.mod = load(FakeTable())

    def ci_context(self, sub):
        _, is_ci, ctx = self.mod.parse_identity(ci_detail(sub))
        self.assertTrue(is_ci)
        return ctx

    def test_legacy_subject(self):
        ctx = self.ci_context("repo:javaBin/jz-watch:ref:refs/heads/main")
        self.assertEqual((ctx["repo"], ctx["ref"]), ("javaBin/jz-watch", "main"))

    def test_immutable_subject_strips_ids(self):
        ctx = self.ci_context("repo:javaBin@95047/2027.javazone.no@1433986972:ref:refs/heads/main")
        self.assertEqual((ctx["repo"], ctx["ref"]), ("javaBin/2027.javazone.no", "main"))


if __name__ == "__main__":
    unittest.main()
