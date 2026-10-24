"""Tests for ci_broker — run: python3 -m unittest discover terraform/lambda-src/tests"""

import importlib.util
import os
import sys
import types
import unittest
from unittest import mock

SRC = os.path.join(os.path.dirname(__file__), "..")


class FakeSTS:
    def __init__(self):
        self.calls = []

    def assume_role(self, RoleArn, RoleSessionName, DurationSeconds):
        self.calls.append({"RoleArn": RoleArn, "DurationSeconds": DurationSeconds})
        return {"Credentials": {"AccessKeyId": "a", "SecretAccessKey": "s", "SessionToken": "t"}}


def load(sts):
    """Import the handler against a stub boto3 without leaking it.

    The handler reads env and builds its STS client at import time, so the
    patches only need to hold during exec_module. patch.dict restores
    sys.modules afterwards, so later tests that import boto3 (or shared.*)
    get the real modules regardless of test order.
    """
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda svc, **kw: sts if svc == "sts" else mock.MagicMock()
    env = {"AWS_ACCOUNT_ID": "123456789012", "PROJECT": "javabin"}
    with mock.patch.dict(os.environ, env), \
            mock.patch.dict(sys.modules, {"boto3": boto3}), \
            mock.patch.object(sys, "path", [SRC] + sys.path):
        spec = importlib.util.spec_from_file_location("ci_broker_handler", os.path.join(SRC, "ci_broker", "handler.py"))
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    mod.verify = lambda token, aud: ({"repository": "javaBin/cupcake"} if token == "good"
                                     else (_ for _ in ()).throw(mod.TokenError("bad")))
    mod.resolve_team = lambda repo, requested: "cupcake"
    return mod


class CiBrokerTest(unittest.TestCase):
    def setUp(self):
        self.sts = FakeSTS()
        self.broker = load(self.sts)

    def call(self, action):
        return self.broker.handler({"action": action, "id_token": "good", "team": "cupcake"}, None)

    def test_plan_vends_the_read_only_plan_role_not_the_team_role(self):
        result = self.call("plan")
        self.assertTrue(result["approved"])
        self.assertEqual(self.sts.calls[0]["RoleArn"], "arn:aws:iam::123456789012:role/javabin-ci-plan-cupcake")

    def test_deploy_still_vends_the_deploy_role(self):
        self.assertTrue(self.call("deploy")["approved"])
        self.assertEqual(self.sts.calls[0]["RoleArn"], "arn:aws:iam::123456789012:role/javabin-ci-deploy-cupcake")

    def test_no_action_ever_vends_the_write_capable_team_role(self):
        for action in self.broker.ROLE_MAP:
            self.call(action)
        self.assertFalse(any("ci-team-" in c["RoleArn"] for c in self.sts.calls))

    def test_bad_token_gets_no_credentials(self):
        result = self.broker.handler({"action": "plan", "id_token": "forged"}, None)
        self.assertFalse(result["approved"])
        self.assertEqual(self.sts.calls, [])


if __name__ == "__main__":
    unittest.main()
