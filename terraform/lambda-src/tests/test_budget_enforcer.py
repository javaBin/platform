"""Tests for budget_enforcer — run: python3 -m unittest discover -s terraform/lambda-src/tests"""

import importlib.util
import json
import os
import sys
import types
import unittest

LAMBDA_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")
ARN = "arn:aws:ecs:eu-central-1:111111111111:service/javabin-platform/"


class FakeEcs:
    def __init__(self, services):
        self.services = services
        self.updated = []
        self.tagged = []

    def get_paginator(self, name):
        services = self.services
        return types.SimpleNamespace(
            paginate=lambda **kw: [{"serviceArns": [s["serviceArn"] for s in services]}])

    def describe_services(self, cluster, services, include):
        return {"services": [s for s in self.services if s["serviceArn"] in services]}

    def update_service(self, cluster, service, desiredCount):
        self.updated.append((service, desiredCount))

    def tag_resource(self, resourceArn, tags):
        self.tagged.append((resourceArn, tags))


def load(ecs):
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda name, *a, **kw: ecs if name == "ecs" else object()
    sys.modules["boto3"] = boto3
    os.environ["COST_WEBHOOK_PARAM"] = "/x/cost"
    if LAMBDA_SRC not in sys.path:
        sys.path.insert(0, LAMBDA_SRC)
    for name in [m for m in sys.modules if m == "shared" or m.startswith("shared.")]:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        "budget_enforcer_handler", os.path.join(LAMBDA_SRC, "budget_enforcer", "handler.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.get_webhook_url = lambda *a: "https://hooks.example/x"
    mod.post_to_slack = lambda *a: None
    return mod


def svc(name, count, desired_tag, team="cupcake"):
    tags = [{"key": "team", "value": team}]
    if desired_tag is not None:
        tags.append({"key": "javabin:desired-count", "value": desired_tag})
    return {"serviceArn": ARN + name, "serviceName": name, "desiredCount": count, "tags": tags}


def breach(team="cupcake"):
    msg = {"budgetName": f"javabin-team-{team}", "budgetLimit": {"amount": "50"}}
    return {"Records": [{"Sns": {"Message": json.dumps(msg)}}]}


class BudgetEnforcerTests(unittest.TestCase):
    def test_scales_to_zero_and_pins_desired_count_tag(self):
        ecs = FakeEcs([svc("cupcake-cupcake", 2, "2"), svc("other-app", 1, "1", team="other")])
        load(ecs).handler(breach(), None)
        self.assertEqual(ecs.updated, [("cupcake-cupcake", 0)])
        self.assertEqual(ecs.tagged, [(ARN + "cupcake-cupcake", [{"key": "javabin:desired-count", "value": "0"}])])

    def test_pins_tag_of_a_service_not_deployed_yet(self):
        # Created with 0 tasks; ecs-deploy would otherwise scale it to the tag
        ecs = FakeEcs([svc("cupcake-new", 0, "1")])
        load(ecs).handler(breach(), None)
        self.assertEqual(ecs.updated, [])
        self.assertEqual(len(ecs.tagged), 1)

    def test_leaves_already_pinned_or_untagged_services_untagged(self):
        ecs = FakeEcs([svc("cupcake-a", 1, "0"), svc("cupcake-b", 1, None)])
        load(ecs).handler(breach(), None)
        self.assertEqual(ecs.tagged, [])
        self.assertEqual(sorted(ecs.updated), [("cupcake-a", 0), ("cupcake-b", 0)])


if __name__ == "__main__":
    unittest.main()
