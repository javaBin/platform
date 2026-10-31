"""Tests for budget_enforcer — run: python3 -m unittest discover -s terraform/lambda-src/tests"""

import importlib.util
import json
import os
import sys
import types
import unittest

LAMBDA_SRC = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..")

PATH = "/javabin/budget-suspensions"
ARN = "arn:aws:ecs:eu-central-1:111111111111:service/javabin-platform/"
TAG = "javabin:desired-count"

BUDGET_TEXT = """AWS Budget Notification November 12, 2026
AWS Account 111111111111

Dear AWS Customer,

You requested that we alert you when the ACTUAL Cost associated with your javabin-team-cupcake budget is greater than $95.24 for the current month. The ACTUAL Cost associated with this budget is $96.10.

Budget Name: javabin-team-cupcake
Budget Type: Cost
Budgeted Amount: $47.62
Alert Type: ACTUAL
Alert Threshold: > $95.24
ACTUAL Amount: $96.10
"""


class ParameterNotFound(Exception):
    pass


class InvalidDBInstanceStateFault(Exception):
    pass


class Paginator:
    def __init__(self, fn):
        self.fn = fn

    def paginate(self, **kw):
        return [self.fn(**kw)]


class FakeSSM:
    exceptions = types.SimpleNamespace(ParameterNotFound=ParameterNotFound)

    def __init__(self):
        self.params = {"/x/cost": "https://hooks.example/cost"}

    def get_parameter(self, Name, WithDecryption=False):
        if Name not in self.params:
            raise ParameterNotFound(Name)
        return {"Parameter": {"Name": Name, "Value": self.params[Name]}}

    def put_parameter(self, Name, Value, **kw):
        self.params[Name] = Value

    def delete_parameter(self, Name):
        if Name not in self.params:
            raise ParameterNotFound(Name)
        del self.params[Name]

    def get_paginator(self, name):
        assert name == "get_parameters_by_path"
        return Paginator(lambda Path: {"Parameters": [
            {"Name": n} for n in self.params if n.startswith(Path + "/") and "/" not in n[len(Path) + 1:]
        ]})


class FakeECS:
    def __init__(self, services, desired_tags=None):
        # name -> (team, desired); name -> javabin:desired-count tag value
        self.services = dict(services)
        self.desired_tags = dict(desired_tags or {})
        self.updates = []
        self.tagged = []

    def get_paginator(self, name):
        return Paginator(lambda cluster: {"serviceArns": [ARN + n for n in self.services]})

    def describe_services(self, cluster, services, include):
        out = []
        for arn in services:
            n = arn[len(ARN):]
            tags = [{"key": "team", "value": self.services[n][0]}]
            if n in self.desired_tags:
                tags.append({"key": TAG, "value": self.desired_tags[n]})
            out.append({"serviceArn": arn, "serviceName": n,
                        "desiredCount": self.services[n][1], "tags": tags})
        return {"services": out}

    def tag_resource(self, resourceArn, tags):
        for t in tags:
            assert t["key"] == TAG
            self.desired_tags[resourceArn[len(ARN):]] = t["value"]
        self.tagged.append((resourceArn[len(ARN):], tags[0]["value"]))

    def update_service(self, cluster, service, desiredCount):
        team, _ = self.services[service]
        self.services[service] = (team, desiredCount)
        self.updates.append((service, desiredCount))


class FakeRDS:
    exceptions = types.SimpleNamespace(InvalidDBInstanceStateFault=InvalidDBInstanceStateFault)

    def __init__(self, dbs):
        self.dbs = dbs  # list of dicts
        self.stopped, self.started = [], []

    def get_paginator(self, name):
        return Paginator(lambda: {"DBInstances": self.dbs})

    def _db(self, ident):
        return next(d for d in self.dbs if d["DBInstanceIdentifier"] == ident)

    def stop_db_instance(self, DBInstanceIdentifier):
        self._db(DBInstanceIdentifier)["DBInstanceStatus"] = "stopping"
        self.stopped.append(DBInstanceIdentifier)

    def start_db_instance(self, DBInstanceIdentifier):
        db = self._db(DBInstanceIdentifier)
        if db["DBInstanceStatus"] != "stopped":
            raise InvalidDBInstanceStateFault(DBInstanceIdentifier)
        db["DBInstanceStatus"] = "starting"
        self.started.append(DBInstanceIdentifier)


class FakeBudgets:
    def __init__(self, limit, actual):
        self.limit, self.actual = limit, actual

    def describe_budget(self, AccountId, BudgetName):
        if self.limit is None:
            raise RuntimeError("no budget")
        return {"Budget": {"BudgetLimit": {"Amount": str(self.limit)},
                           "CalculatedSpend": {"ActualSpend": {"Amount": str(self.actual)}}}}


def db(ident, team, status="available", **extra):
    return {"DBInstanceIdentifier": ident, "DBInstanceStatus": status,
            "TagList": [{"Key": "team", "Value": team}], **extra}


def load(ecs, rds, ssm, budgets):
    clients = {"ecs": ecs, "rds": rds, "ssm": ssm, "budgets": budgets}
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda name, *a, **kw: clients[name]
    sys.modules["boto3"] = boto3
    os.environ.update({"COST_WEBHOOK_PARAM": "/x/cost", "SUSPENSION_PATH": PATH,
                       "AWS_ACCOUNT_ID": "111111111111"})
    if LAMBDA_SRC not in sys.path:
        sys.path.insert(0, LAMBDA_SRC)
    for name in [m for m in sys.modules if m == "shared" or m.startswith("shared.")]:
        del sys.modules[name]
    spec = importlib.util.spec_from_file_location(
        "budget_enforcer_handler", os.path.join(LAMBDA_SRC, "budget_enforcer", "handler.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.post_to_slack = lambda url, msg: mod._posted.append(msg)
    mod._posted = []
    return mod


def sns(message):
    return {"Records": [{"Sns": {"Message": message}}]}


class BudgetEnforcerTest(unittest.TestCase):
    def setUp(self):
        self.ecs = FakeECS({"cupcake-web": ("cupcake", 2), "cupcake-idle": ("cupcake", 0),
                            "other-web": ("other", 1)},
                           {"cupcake-web": "2", "other-web": "1"})
        self.rds = FakeRDS([db("cupcake-db", "cupcake"), db("other-db", "other"),
                            db("cupcake-aurora", "cupcake", DBClusterIdentifier="c")])
        self.ssm = FakeSSM()
        self.budgets = FakeBudgets(47.62, 96.10)
        self.mod = load(self.ecs, self.rds, self.ssm, self.budgets)

    def marker(self):
        return json.loads(self.ssm.params[f"{PATH}/cupcake"])

    def test_parses_plain_text_budget_notification(self):
        self.assertEqual(self.mod.parse_budget_message(BUDGET_TEXT), ("javabin-team-cupcake", "47.62"))

    def test_parses_json_notification(self):
        msg = json.dumps({"budgetName": "javabin-team-x", "budgetLimit": {"amount": "5"}})
        self.assertEqual(self.mod.parse_budget_message(msg), ("javabin-team-x", "5"))

    def test_suspend_writes_marker_scales_services_and_stops_team_rds(self):
        self.mod.handler(sns(BUDGET_TEXT), None)

        self.assertEqual(self.ecs.services["cupcake-web"], ("cupcake", 0))
        self.assertEqual(self.ecs.services["other-web"], ("other", 1))
        self.assertEqual(self.rds.stopped, ["cupcake-db"])
        state = self.marker()
        self.assertEqual(state["services"], {"cupcake-web": 2})
        self.assertEqual(state["rds"], ["cupcake-db"])
        self.assertEqual(len(self.mod._posted), 1)

    def test_suspend_pins_desired_count_tag_so_ecs_deploy_keeps_zero(self):
        self.mod.handler(sns(BUDGET_TEXT), None)
        self.assertEqual(self.ecs.desired_tags, {"cupcake-web": "0", "other-web": "1"})
        self.assertEqual(self.marker()["desired_count_tags"], {"cupcake-web": "2"})

    def test_suspend_pins_tag_of_a_service_not_deployed_yet(self):
        # Created with 0 tasks; ecs-deploy would otherwise scale it to the tag
        self.ecs.desired_tags["cupcake-idle"] = "1"
        self.mod.handler(sns(BUDGET_TEXT), None)
        self.assertNotIn(("cupcake-idle", 0), self.ecs.updates)
        self.assertEqual(self.ecs.desired_tags["cupcake-idle"], "0")

    def test_suspend_leaves_already_pinned_or_untagged_services_untagged(self):
        self.ecs.services["cupcake-web"] = ("cupcake", 1)
        self.ecs.desired_tags["cupcake-web"] = "0"
        self.mod.handler(sns(BUDGET_TEXT), None)
        self.assertEqual(self.ecs.tagged, [])
        self.assertEqual(self.ecs.services["cupcake-web"], ("cupcake", 0))

    def test_ignores_non_team_budget(self):
        self.mod.handler(sns(BUDGET_TEXT.replace("javabin-team-cupcake", "javabin-account-monthly")), None)
        self.assertEqual(self.ecs.updates, [])
        self.assertNotIn(f"{PATH}/cupcake", self.ssm.params)

    def test_reconcile_reapplies_while_over_limit(self):
        self.mod.handler(sns(BUDGET_TEXT), None)
        # Someone scales up by hand; RDS auto-starts after 7 days.
        self.ecs.services["cupcake-web"] = ("cupcake", 3)
        self.rds._db("cupcake-db")["DBInstanceStatus"] = "available"

        self.mod.handler({"action": "reconcile"}, None)

        self.assertEqual(self.ecs.services["cupcake-web"], ("cupcake", 0))
        self.assertEqual(self.rds.stopped, ["cupcake-db", "cupcake-db"])
        # The original count is what lift restores, not the manual one.
        self.assertEqual(self.marker()["services"], {"cupcake-web": 2})

    def test_reconcile_lifts_when_spend_back_under_limit(self):
        self.mod.handler(sns(BUDGET_TEXT), None)
        self.rds._db("cupcake-db")["DBInstanceStatus"] = "stopped"
        self.budgets.limit = 100  # budget_nok raised in the registry

        out = self.mod.handler({"action": "reconcile"}, None)

        self.assertEqual(out["results"][0]["action"], "lifted")
        self.assertNotIn(f"{PATH}/cupcake", self.ssm.params)
        self.assertEqual(self.ecs.services["cupcake-web"], ("cupcake", 2))
        self.assertEqual(self.ecs.services["cupcake-idle"], ("cupcake", 0))
        self.assertEqual(self.ecs.desired_tags, {"cupcake-web": "2", "other-web": "1"})
        self.assertEqual(self.rds.started, ["cupcake-db"])

    def test_reconcile_repins_tag_reset_by_hand(self):
        self.mod.handler(sns(BUDGET_TEXT), None)
        self.ecs.desired_tags["cupcake-web"] = "3"
        self.mod.handler({"action": "reconcile"}, None)
        self.assertEqual(self.ecs.desired_tags["cupcake-web"], "0")
        # Lift restores the value from before the suspension.
        self.assertEqual(self.marker()["desired_count_tags"], {"cupcake-web": "2"})

    def test_reconcile_keeps_suspension_if_budget_unreadable(self):
        self.mod.handler(sns(BUDGET_TEXT), None)
        self.budgets.limit = None
        self.mod.handler({"action": "reconcile"}, None)
        self.assertIn(f"{PATH}/cupcake", self.ssm.params)

    def test_manual_lift(self):
        self.mod.handler(sns(BUDGET_TEXT), None)
        out = self.mod.handler({"action": "lift", "team": "cupcake"}, None)
        self.assertEqual(out["results"][0]["action"], "lifted")
        self.assertEqual(self.ecs.services["cupcake-web"], ("cupcake", 2))
        # RDS still stopping: start is skipped, not an error.
        self.assertEqual(self.rds.started, [])

    def test_lift_without_suspension_is_noop(self):
        out = self.mod.handler({"action": "lift", "team": "cupcake"}, None)
        self.assertEqual(out["results"][0]["action"], "not_suspended")
        self.assertEqual(self.ecs.updates, [])


if __name__ == "__main__":
    unittest.main()
