"""Tests for slack_post.py — run: python3 -m unittest discover -s scripts/tests"""

import importlib.util
import json
import os
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("slack_post", os.path.join(HERE, "..", "slack_post.py"))
sp = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sp)

ENV = {"GITHUB_REPOSITORY": "javaBin/platform", "GITHUB_SHA": "abcdef1234", "GITHUB_RUN_ID": "42", "GITHUB_ACTOR": "me"}


class FakeResponse:
    def __init__(self, body):
        self.body = body

    def read(self):
        return self.body.encode()

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def opener_replying(*replies):
    sent = []

    def opener(req, timeout):
        sent.append(json.loads(req.data))
        return FakeResponse(replies[len(sent) - 1])

    return opener, sent


def texts(payload):
    out = []
    for b in payload["blocks"]:
        if "text" in b:
            out.append(b["text"]["text"])
        out += [f["text"] for f in b.get("fields", [])]
    return out


class RiskPayloadTest(unittest.TestCase):
    def test_failed_review_is_labelled_failed_not_high(self):
        p = sp.risk_payload({"risk": "FAILED"}, "https://x/override", ENV)
        self.assertIn("Review Failed", p["blocks"][0]["text"]["text"])
        self.assertIn("plans/javaBin/platform/42/tfplan", "\n".join(texts(p)))

    def test_missing_file_is_treated_as_failed(self):
        p = sp.risk_payload(sp.load_review("/nonexistent/review.json"), "https://x", ENV)
        self.assertIn("Review Failed", p["blocks"][0]["text"]["text"])

    def test_long_findings_are_cut_to_slack_section_limit(self):
        findings = [{"category": "security", "detail": "x" * 1500} for _ in range(5)]
        p = sp.risk_payload({"risk": "HIGH", "summary": "y" * 5000, "findings": findings}, "https://x", ENV)
        self.assertTrue(all(len(t) <= sp.SECTION_MAX for t in texts(p)))
        self.assertLessEqual(len(p["text"]), sp.TEXT_MAX)

    def test_special_characters_produce_valid_json(self):
        p = sp.risk_payload({"risk": "HIGH", "summary": 'a "quote" `tick` \\ <tag> & \n line',
                             "findings": [{"category": "cost", "detail": 'b"c'}]}, "https://x", ENV)
        self.assertEqual(json.loads(json.dumps(p)), p)

    def test_review_file_with_findings_string_does_not_crash(self):
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump({"risk": "HIGH", "summary": "s", "findings": "oops"}, f)
        p = sp.risk_payload(sp.load_review(f.name), "https://x", ENV)
        self.assertNotIn("*Findings*", "\n".join(texts(p)))


class SendTest(unittest.TestCase):
    def test_ok_sends_once(self):
        opener, sent = opener_replying("ok")
        self.assertTrue(sp.send("https://hook", sp.message_payload("T", "m"), opener))
        self.assertEqual(len(sent), 1)

    def test_rejected_blocks_fall_back_to_plain_text(self):
        opener, sent = opener_replying("invalid_blocks", "ok")
        self.assertTrue(sp.send("https://hook", sp.risk_payload({"risk": "HIGH"}, "https://x", ENV), opener))
        self.assertEqual(len(sent), 2)
        self.assertNotIn("blocks", sent[1])
        self.assertIn("Run ID: 42", sent[1]["text"])

    def test_both_rejected_reports_failure(self):
        opener, _ = opener_replying("invalid_blocks", "channel_is_archived")
        self.assertFalse(sp.send("https://hook", sp.message_payload("T", "m"), opener))

    def test_no_webhook_is_skipped(self):
        self.assertEqual(sp.main(["slack_post.py", "message", "T", "m"], env={}), 0)


class MessagePayloadTest(unittest.TestCase):
    def test_literal_backslash_n_becomes_newline(self):
        # approve-override.yml passes "*Repo:* x\n*By:* y" as a literal \n.
        p = sp.message_payload("Risk Override Approved", "*Repo:* x\\n*By:* y")
        self.assertEqual(p["blocks"][1]["text"]["text"], "*Repo:* x\n*By:* y")

    def test_url_block(self):
        p = sp.message_payload("T", "m", "https://u", "Open")
        self.assertEqual(p["blocks"][2]["text"]["text"], "<https://u|Open>")


if __name__ == "__main__":
    unittest.main()
