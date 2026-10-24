"""Tests for review-plan.py — run: python3 -m unittest discover scripts/tests"""

import importlib.util
import os
import unittest
from unittest import mock

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("review_plan", os.path.join(HERE, "..", "review-plan.py"))
rp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(rp)

REFRESH = (
    "module.lambdas.aws_lambda_function.x: Refreshing state... [id=x]\n"
    "data.aws_iam_policy_document.y: Reading...\n"
    "data.aws_iam_policy_document.y: Read complete after 0s [id=1]\n"
    "aws_s3_bucket.root: Refreshing state... [id=b]\n"
)


def resource(addr, action="will be updated in-place", body_lines=3):
    body = "\n".join(f'      ~ attr_{i} = "a" -> "b"' for i in range(body_lines))
    return f"  # {addr} {action}\n  ~ resource \"x\" \"y\" {{\n{body}\n    }}\n"


def plan(n, body_lines=3):
    head = "Terraform will perform the following actions:\n\n"
    return head + "".join(resource(f"aws_iam_role_policy.p{i}", body_lines=body_lines) for i in range(n)) + \
        f"\nPlan: 0 to add, {n} to change, 0 to destroy.\n"


def tool_response():
    return {"output": {"message": {"content": [{"toolUse": {"name": "submit_review", "input": {
        "risk": "LOW", "summary": "ok", "findings": []}}}]}}}


def sent_prompt(plan_text):
    seen = {}

    def converse(messages, tool_config):
        seen["prompt"] = messages[0]["content"][0]["text"]
        return tool_response()

    rp.review_plan(plan_text, converse=converse)
    return seen["prompt"]


class StripRefreshNoiseTest(unittest.TestCase):
    def test_refresh_and_read_lines_are_removed(self):
        text = REFRESH + plan(1)
        clean = rp.strip_refresh_noise(text)
        self.assertNotIn("Refreshing state", clean)
        self.assertNotIn("Read complete", clean)
        self.assertIn("# aws_iam_role_policy.p0 will be updated in-place", clean)

    def test_indented_diff_lines_are_kept(self):
        text = '      ~ description = "Reading...: x" -> "y"\n'
        self.assertEqual(rp.strip_refresh_noise(text), text.rstrip("\n"))


class ResourceChangesTest(unittest.TestCase):
    def test_parses_every_action(self):
        text = (resource("aws_db_instance.db", "must be replaced") + resource("aws_s3_bucket.b", "will be destroyed")
                + resource('module.m.aws_iam_role.r["a b"]', "will be created")
                + "  # aws_x.old (deposed object 1a2b) will be destroyed\n"
                + "  # (because aws_x.old is not in configuration)\n"
                + "  # aws_x.a has moved to aws_x.b\n")
        self.assertEqual([(a, act) for _, a, act in rp.resource_changes(text)], [
            ("aws_db_instance.db", "must be replaced"),
            ("aws_s3_bucket.b", "will be destroyed"),
            ('module.m.aws_iam_role.r["a b"]', "will be created"),
            ("aws_x.old", "will be destroyed"),
            ("aws_x.a", "has moved to aws_x.b"),
        ])


class ReviewPlanTest(unittest.TestCase):
    def test_plan_under_limit_is_sent_whole_without_refresh_noise(self):
        text = REFRESH * 500 + plan(200)
        prompt = sent_prompt(text)
        self.assertIn("(complete)", prompt)
        self.assertIn(rp.strip_refresh_noise(text), prompt)
        self.assertNotIn("Refreshing state", prompt)
        self.assertNotIn("NOTE: this plan is", prompt)

    def test_old_80k_limit_no_longer_cuts(self):
        text = plan(1000)
        self.assertGreater(len(text), 80_000)
        prompt = sent_prompt(text)
        self.assertIn("aws_iam_role_policy.p999 will be updated in-place", prompt)
        self.assertIn("Plan: 0 to add, 1000 to change", prompt)

    def test_plan_over_limit_lists_every_change_and_states_what_was_omitted(self):
        text = plan(100, body_lines=20)
        limit = 20_000
        self.assertGreater(len(text), limit)
        section = rp.build_plan_section(text, limit=limit)
        self.assertLessEqual(len(section), limit)
        self.assertIn("NOTE: this plan is", section)
        self.assertIn("All 100 resource changes (Plan: 0 to add, 100 to change, 0 to destroy.)", section)
        change_list, partial = section.split("(partial, first")
        omitted = [i for i in range(100) if f"- aws_iam_role_policy.p{i}: will be updated in-place [diff not shown]" in change_list]
        self.assertTrue(omitted)
        # Cut at a resource boundary: every resource is listed, and is either fully shown or marked omitted.
        for i in range(100):
            addr = f"aws_iam_role_policy.p{i}"
            self.assertIn(f"- {addr}: will be updated in-place", change_list)
            self.assertNotEqual(f"# {addr} will be updated in-place" in partial, i in omitted)
        self.assertIn(f"covers only the first {100 - len(omitted)} and stops", section)

    def test_prompt_warns_about_hidden_unchanged_attributes(self):
        prompt = sent_prompt(plan(1))
        self.assertIn("shows only the\n  changed attributes", prompt)
        self.assertIn("Deny", prompt)


    def test_complete_plan_is_declared_complete_with_its_change_count(self):
        prompt = sent_prompt(plan(7))
        self.assertIn("COMPLETE PLAN", prompt)
        self.assertIn("all 7 resource change headers", prompt)
        self.assertIn("(Plan: 0 to add, 7 to change, 0 to destroy.)", prompt)
        self.assertIn("do not say the plan is truncated", prompt)

    def test_oversized_plan_is_not_declared_complete(self):
        section = rp.build_plan_section(plan(100, body_lines=20), limit=20_000)
        self.assertNotIn("COMPLETE PLAN", section)
        self.assertIn("NOTE: this plan is", section)

    def test_prompt_explains_terraform_elision_markers_are_not_truncation(self):
        prompt = sent_prompt(plan(1))
        for marker in ("unchanged attributes hidden", "unchanged blocks hidden", "unchanged elements hidden",
                       "(sensitive value)", "(known after apply)", "jsonencode"):
            self.assertIn(marker, prompt)
        self.assertIn("not truncation", prompt)

    def test_no_input_cap_by_default(self):
        self.assertIsNone(rp.MAX_PLAN_CHARS)
        text = plan(20_000)
        self.assertGreater(len(text), 2_000_000)
        section = rp.build_plan_section(text)
        self.assertIn("COMPLETE PLAN", section)
        self.assertIn("aws_iam_role_policy.p19999 will be updated in-place", section)

    def test_large_destroy_plan_is_sent_whole(self):
        # PR #135 (2026-10-24): 231 destroys, 830k chars, was cut to 28 changes by the old 400k limit.
        destroy = "".join(
            f'  # module.iam.aws_iam_role.ci_deploy["team{i}"] will be destroyed\n'
            f'  - resource "aws_iam_role" "ci_deploy" {{\n'
            + "".join(f'      - attr_{j}                = "value-{j}" -> null\n' for j in range(66))
            + "    }\n" for i in range(231))
        text = "Terraform will perform the following actions:\n\n" + destroy + \
            "\nPlan: 0 to add, 0 to change, 231 to destroy.\n"
        self.assertGreater(len(text), 800_000)
        section = rp.build_plan_section(text)
        self.assertIn("COMPLETE PLAN", section)
        self.assertIn('ci_deploy["team230"] will be destroyed', section)
        self.assertNotIn("NOTE: this plan is", section)


class StopReasonTest(unittest.TestCase):
    def test_answer_cut_at_max_tokens_is_discarded(self):
        def converse(messages, tool_config):
            r = tool_response()
            r["stopReason"] = "max_tokens"
            return r

        with mock.patch("sys.stderr"):
            self.assertIsNone(rp.review_plan(plan(1), converse=converse))


class ReviewFeedbackTest(unittest.TestCase):
    def test_tainted_and_removed_headers_parse_cleanly(self):
        plan = ("  # aws_instance.a is tainted, so must be replaced\n"
                "  # aws_s3_bucket.old will no longer be managed by Terraform\n")
        self.assertEqual([(a, act) for _, a, act in rp.resource_changes(plan)],
                         [("aws_instance.a", "must be replaced"),
                          ("aws_s3_bucket.old", "will no longer be managed by Terraform")])

    def test_huge_change_list_is_grouped_and_stays_under_the_limit(self):
        headers = "".join(f'  # module.m{i % 3}.aws_iam_role.r["team{i}"] will be created\n  + resource {{}}\n'
                          for i in range(6000))
        section = rp.build_plan_section(headers + "Plan: 6000 to add, 0 to change, 0 to destroy.\n", limit=50_000)
        self.assertLessEqual(len(section), 50_000)
        self.assertIn('module.m0.aws_iam_role.r[*]: will be created ×2000', section)
        self.assertIn("No diff is shown", section)


if __name__ == "__main__":
    unittest.main()
