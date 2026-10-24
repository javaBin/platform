"""Tests for slack_alert ECS start-impaired alerts — run: python3 -m unittest discover -s terraform/lambda-src/tests"""

import importlib.util
import os
import sys
import types
import unittest

LAMBDA_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")


class FakeTable:
    def __init__(self):
        self.items = {}

    def get_item(self, Key):
        item = self.items.get(Key["finding_key"])
        return {"Item": item} if item else {}

    def put_item(self, Item):
        self.items[Item["finding_key"]] = Item


def load(table):
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda *a, **kw: object()
    boto3.resource = lambda *a, **kw: types.SimpleNamespace(Table=lambda name: table)
    botocore = types.ModuleType("botocore")
    botocore_config = types.ModuleType("botocore.config")
    botocore_config.Config = lambda **kw: None
    sys.modules.update({"boto3": boto3, "botocore": botocore, "botocore.config": botocore_config})
    os.environ.update({
        "INFRA_WEBHOOK_PARAM": "/x/infra",
        "COST_WEBHOOK_PARAM": "/x/cost",
        "SECURITY_TOPIC_ARN": "arn:aws:sns:eu-central-1:111111111111:javabin-security",
    })
    if LAMBDA_SRC not in sys.path:
        sys.path.insert(0, LAMBDA_SRC)
    for name in [m for m in sys.modules if m == "shared" or m.startswith("shared.")]:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        "slack_alert_handler", os.path.join(LAMBDA_SRC, "slack_alert", "handler.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def event(service="testteam-platform-test-app", name="SERVICE_TASK_START_IMPAIRED",
          time="2026-10-24T10:00:00Z"):
    return {
        "version": "0",
        "detail-type": "ECS Service Action",
        "source": "aws.ecs",
        "account": "111111111111",
        "time": time,
        "region": "eu-central-1",
        "resources": [f"arn:aws:ecs:eu-central-1:111111111111:service/javabin-platform/{service}"],
        "detail": {
            "eventType": "WARN",
            "eventName": name,
            "clusterArn": "arn:aws:ecs:eu-central-1:111111111111:cluster/javabin-platform",
        },
    }


class EcsStartImpairedTests(unittest.TestCase):
    def setUp(self):
        self.table = FakeTable()
        self.mod = load(self.table)

    def test_formats_service_and_console_link(self):
        msg = self.mod.format_structured_alert("subject", event())
        self.assertEqual(msg["text"], "ECS service testteam-platform-test-app cannot start tasks")
        rendered = str(msg["blocks"])
        self.assertIn("`javabin-platform`", rendered)
        self.assertIn(
            "https://eu-central-1.console.aws.amazon.com/ecs/v2/clusters/javabin-platform"
            "/services/testteam-platform-test-app/events?region=eu-central-1",
            rendered,
        )

    def test_alerts_once_per_service_per_day(self):
        self.assertIsNotNone(self.mod.format_structured_alert("s", event()))
        self.assertIsNone(self.mod.format_structured_alert("s", event(time="2026-10-24T18:00:00Z")))
        self.assertIsNotNone(self.mod.format_structured_alert("s", event(service="other-app")))
        self.assertIsNotNone(self.mod.format_structured_alert("s", event(time="2026-10-25T08:00:00Z")))

    def test_ignores_other_ecs_service_actions(self):
        self.assertIsNone(self.mod.format_structured_alert("s", event(name="SERVICE_STEADY_STATE")))
        self.assertEqual(self.table.items, {})


if __name__ == "__main__":
    unittest.main()
