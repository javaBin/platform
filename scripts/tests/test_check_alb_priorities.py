"""Tests for check-alb-priorities.py — run: python3 -m unittest discover scripts/tests"""

import contextlib
import importlib.util
import io
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("check_alb_priorities", os.path.join(HERE, "..", "check-alb-priorities.py"))
check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(check)

LISTENER = "arn:aws:elasticloadbalancing:eu-central-1:1:listener/app/javabin-platform-alb/x/y"


def rule(priority, host, arn=None):
    return {
        "RuleArn": arn or f"arn:rule/{priority}",
        "Priority": str(priority),
        "Conditions": [{"Field": "host-header", "Values": [host], "HostHeaderConfig": {"Values": [host]}}],
    }


LIVE = [
    {"RuleArn": "arn:rule/default", "Priority": "default", "Conditions": []},
    rule(10, "password.javazone.no"),
    rule(100, "cupcake.javazone.no"),
    rule(101, "cupcake-backend.javazone.no"),
    rule(150, "watch.javazone.no"),
]


def change(address, actions, before=None, after=None):
    return {"address": address, "type": "aws_lb_listener_rule",
            "change": {"actions": actions, "before": before, "after": after}}


def plan(*changes):
    return {"resource_changes": [
        {"address": "module.ecr.aws_ecr_repository.this", "type": "aws_ecr_repository",
         "change": {"actions": ["create"], "before": None, "after": {}}},
        *changes,
    ]}


def run(p, live=LIVE):
    wanted, freed = check.planned_rules(p)
    return check.conflicts(wanted, freed, check.live_priorities(live))


class CheckAlbPrioritiesTest(unittest.TestCase):
    def test_new_rule_on_a_taken_priority_fails_with_the_holder_and_a_free_number(self):
        errors = run(plan(change("module.routing.aws_lb_listener_rule.this", ["create"],
                                 after={"priority": 100, "listener_arn": LISTENER})))
        self.assertEqual(len(errors), 1)
        self.assertIn("priority 100 is already used by the rule for cupcake.javazone.no", errors[0])
        self.assertIn("PriorityInUse", errors[0])
        self.assertIn("e.g. 21", errors[0])  # 11-21 is the first free run of 11 (10 is taken)

    def test_free_priority_passes(self):
        self.assertEqual(run(plan(change("module.routing.aws_lb_listener_rule.this", ["create"],
                                         after={"priority": 2297, "listener_arn": LISTENER}))), [])

    def test_login_rule_below_the_host_rule_is_checked_too(self):
        errors = run(plan(
            change("module.routing.aws_lb_listener_rule.this", ["create"],
                   after={"priority": 151, "listener_arn": LISTENER}),
            change('module.routing.aws_lb_listener_rule.login["p1"]', ["create"],
                   after={"priority": 150, "listener_arn": LISTENER}),
        ))
        self.assertEqual(len(errors), 1)
        self.assertIn('login["p1"]', errors[0])
        self.assertIn("watch.javazone.no", errors[0])

    def test_unchanged_and_in_place_updates_of_the_own_rule_pass(self):
        own = {"arn": "arn:rule/101", "priority": 101, "listener_arn": LISTENER}
        self.assertEqual(run(plan(change("r", ["no-op"], before=own, after=own))), [])
        self.assertEqual(run(plan(change("r", ["update"], before=own, after={**own, "tags": {}}))), [])

    def test_moving_onto_a_priority_the_plan_frees_passes(self):
        # one app moves its host rule from 150 onto 151 and a login rule onto 150
        host = {"arn": "arn:rule/150", "priority": 150, "listener_arn": LISTENER}
        self.assertEqual(run(plan(
            change("module.routing.aws_lb_listener_rule.this", ["update"], before=host, after={**host, "priority": 151}),
            change('module.routing.aws_lb_listener_rule.login["p1"]', ["create"],
                   after={"priority": 150, "listener_arn": LISTENER}),
        )), [])

    def test_delete_then_create_frees_but_create_before_destroy_does_not(self):
        old = {"arn": "arn:rule/100", "priority": 100, "listener_arn": LISTENER}
        new = {"priority": 100, "listener_arn": LISTENER}
        self.assertEqual(run(plan(change("r", ["delete", "create"], before=old, after=new))), [])
        self.assertEqual(len(run(plan(change("r", ["create", "delete"], before=old, after=new)))), 1)

    def test_unknown_values_are_skipped(self):
        self.assertEqual(run(plan(change("r", ["create"], after={"priority": None, "listener_arn": LISTENER}))), [])
        self.assertEqual(run(plan(change("r", ["create"], after={"priority": 100, "listener_arn": None}))), [])

    def test_suggestion_leaves_room_for_login_rules(self):
        taken = {11, 30}
        self.assertEqual(check.suggest_priority(taken), 22)
        self.assertIsNone(check.suggest_priority(set(range(1, 1000, 5))))


class ErrorsFileTest(unittest.TestCase):
    """The conflicts are left in a file for the PR comment (post-plan-comment.sh)."""

    def setUp(self):
        self.cwd = os.getcwd()
        self.tmp = tempfile.TemporaryDirectory()
        os.chdir(self.tmp.name)
        self.run_json = check._run_json
        self.argv = sys.argv

    def tearDown(self):
        check._run_json, sys.argv = self.run_json, self.argv
        os.chdir(self.cwd)
        self.tmp.cleanup()

    def main(self, priority):
        p = plan(change("module.routing.aws_lb_listener_rule.this", ["create"],
                        after={"priority": priority, "listener_arn": LISTENER}))
        check._run_json = lambda cmd, cwd=None: p if cmd[0] == "terraform" else {"Rules": LIVE}
        sys.argv = ["check-alb-priorities.py", "tf"]
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return check.main()

    def test_conflict_is_written_for_the_comment(self):
        self.assertEqual(self.main(100), 1)
        with open(check.ERRORS_FILE) as f:
            text = f.read()
        self.assertTrue(text.startswith("- module.routing.aws_lb_listener_rule.this: "), text)
        self.assertIn("priority 100 is already used by the rule for cupcake.javazone.no", text)

    def test_no_conflict_removes_a_stale_file(self):
        with open(check.ERRORS_FILE, "w") as f:
            f.write("- old\n")
        self.assertEqual(self.main(500), 0)
        self.assertFalse(os.path.exists(check.ERRORS_FILE))


if __name__ == "__main__":
    unittest.main()
