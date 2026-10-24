"""Tests for plan_review — run: python3 -m unittest discover terraform/lambda-src/tests"""

import importlib.util
import io
import json
import os
import sys
import types
import unittest
from unittest import mock

HERE = os.path.dirname(__file__)
SRC = os.path.join(HERE, "..")


class FakeS3:
    def __init__(self, objects):
        self.objects = dict(objects)

    def get_object(self, Bucket, Key):
        return {"Body": io.BytesIO(self.objects[Key].encode())}

    def put_object(self, Bucket, Key, Body, ContentType):
        self.objects[Key] = Body.decode()


class FakeBedrock:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = 0

    def converse(self, **kw):
        self.calls += 1
        r = self.responses.pop(0)
        if isinstance(r, Exception):
            raise r
        return r


def tool_response(risk):
    return {"output": {"message": {"content": [{"toolUse": {"name": "submit_review", "input": {
        "risk": risk, "summary": "Adds an ECS service", "findings": [{"category": "routine", "detail": "ok"}]}}}]}}}


PLAN = "plans/javaBin/frosting/123/tfplan"


def load(s3, bedrock, repo="frosting"):
    os.environ["PLAN_BUCKET"] = "bucket"
    os.environ["REVIEW_ATTEMPTS"] = "3"
    boto3 = types.ModuleType("boto3")
    boto3.client = lambda svc, **kw: {"s3": s3, "bedrock-runtime": bedrock, "ssm": mock.MagicMock()}[svc]
    sys.modules["boto3"] = boto3
    botocore = types.ModuleType("botocore"); config = types.ModuleType("botocore.config")
    config.Config = lambda **kw: None
    sys.modules["botocore"] = botocore; sys.modules["botocore.config"] = config
    rp = importlib.util.spec_from_file_location("review_plan", os.path.join(SRC, "..", "..", "scripts", "review-plan.py"))
    review_plan = importlib.util.module_from_spec(rp)
    rp.loader.exec_module(review_plan)
    sys.modules["review_plan"] = review_plan
    sys.path.insert(0, SRC)
    spec = importlib.util.spec_from_file_location("plan_review_handler", os.path.join(SRC, "plan_review", "handler.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    mod.verify = lambda token, aud: {"repository": f"javaBin/{repo}"} if token == "good" else (_ for _ in ()).throw(mod.TokenError("bad"))
    mod.time.sleep = lambda s: None
    return mod


class PlanReviewTest(unittest.TestCase):
    def setUp(self):
        self.s3 = FakeS3({"plans/javaBin/frosting/123/plan-output.txt": "Plan: 15 to add, 0 to change, 0 to destroy."})

    def test_review_writes_risk_and_returns_markdown(self):
        h = load(self.s3, FakeBedrock([tool_response("LOW")]))
        result = h.handler({"plan_key": PLAN, "id_token": "good"}, None)
        self.assertEqual(result["risk"], "LOW")
        self.assertIn("Adds an ECS service", result["markdown"])
        self.assertEqual(json.loads(self.s3.objects["plans/javaBin/frosting/123/risk.json"])["level"], "LOW")

    def test_large_plan_reaches_bedrock_whole_without_refresh_noise(self):
        diff = "".join(f'  # aws_iam_role_policy.p{i} will be updated in-place\n      ~ policy = "a" -> "b"\n'
                       for i in range(3000))
        refresh = "module.x.aws_s3_bucket.b: Refreshing state... [id=b]\n" * 2000
        self.s3.objects["plans/javaBin/frosting/123/plan-output.txt"] = refresh + diff + "Plan: 0 to add, 3000 to change, 0 to destroy."
        self.assertGreater(len(diff), 80_000)
        bedrock = FakeBedrock([tool_response("LOW")])
        h = load(self.s3, bedrock)
        prompts = []
        orig = h.converse
        h.converse = lambda messages, tool_config: (prompts.append(messages[0]["content"][0]["text"]), orig(messages, tool_config))[1]
        self.assertEqual(h.handler({"plan_key": PLAN, "id_token": "good"}, None)["risk"], "LOW")
        self.assertIn(diff.rstrip("\n"), prompts[0])
        self.assertIn("aws_iam_role_policy.p2999", prompts[0])
        self.assertNotIn("Refreshing state", prompts[0])
        self.assertNotIn("NOTE: this plan is", prompts[0])

    def test_transient_failures_are_retried(self):
        bedrock = FakeBedrock([RuntimeError("ThrottlingException"), tool_response("MEDIUM")])
        h = load(self.s3, bedrock)
        self.assertEqual(h.handler({"plan_key": PLAN, "id_token": "good"}, None)["risk"], "MEDIUM")
        self.assertEqual(bedrock.calls, 2)

    def test_persistent_failure_is_failed_not_high(self):
        h = load(self.s3, FakeBedrock([RuntimeError("boom")] * 3))
        result = h.handler({"plan_key": PLAN, "id_token": "good"}, None)
        self.assertEqual(result["risk"], "FAILED")
        self.assertIn("could not run", result["markdown"])
        self.assertEqual(json.loads(self.s3.objects["plans/javaBin/frosting/123/risk.json"])["level"], "FAILED")

    def test_failed_is_recorded_before_review_so_a_crash_leaves_a_verdict(self):
        s3 = FakeS3({})  # plan text missing: get_object raises
        h = load(s3, FakeBedrock([]))
        with self.assertRaises(KeyError):
            h.handler({"plan_key": PLAN, "id_token": "good"}, None)
        self.assertEqual(json.loads(s3.objects["plans/javaBin/frosting/123/risk.json"])["level"], "FAILED")

    def test_stops_retrying_when_time_runs_out(self):
        bedrock = FakeBedrock([RuntimeError("slow")] * 3)
        h = load(self.s3, bedrock)
        ctx = types.SimpleNamespace(get_remaining_time_in_millis=lambda: 50_000)
        self.assertEqual(h.handler({"plan_key": PLAN, "id_token": "good"}, ctx)["risk"], "FAILED")
        self.assertEqual(bedrock.calls, 0)

    def test_another_repos_plan_is_refused(self):
        h = load(self.s3, FakeBedrock([]), repo="cupcake")
        self.assertIn("does not belong", h.handler({"plan_key": PLAN, "id_token": "good"}, None)["error"])
        self.assertNotIn("plans/javaBin/frosting/123/risk.json", self.s3.objects)

    def test_invalid_token_is_refused(self):
        h = load(self.s3, FakeBedrock([]))
        self.assertIn("Invalid GitHub OIDC token", h.handler({"plan_key": PLAN, "id_token": "forged"}, None)["error"])


if __name__ == "__main__":
    unittest.main()
