"""Tests for the override flow scripts — run: python3 -m unittest discover scripts/tests

invoke-plan-review.sh (retries, FAILED vs HIGH), override-help.sh (what a
blocked apply tells the developer) and override-target.sh (approve-override.yml).
"""

import json
import os
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..")
PLAN = "plans/javaBin/frosting/123/tfplan"


def fake_bin(tmp, name, body):
    path = os.path.join(tmp, "bin", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write("#!/bin/sh\n" + body)
    os.chmod(path, 0o755)


def read(path):
    with open(path) as f:
        return f.read()


def run(script, args, tmp, env=None, check=False):
    files = {k: os.path.join(tmp, k) for k in ("output", "summary")}
    for p in files.values():
        open(p, "w").close()
    full_env = dict(os.environ, PATH=f"{tmp}/bin:{os.environ['PATH']}", GITHUB_OUTPUT=files["output"],
                    GITHUB_STEP_SUMMARY=files["summary"], GITHUB_REPOSITORY="javaBin/frosting",
                    GITHUB_RUN_ID="123", GITHUB_SERVER_URL="https://github.com")
    full_env.update(env or {})
    r = subprocess.run(["sh", os.path.join(SCRIPTS, script), *args], cwd=tmp, env=full_env,
                       capture_output=True, text=True, check=check)
    out = {k: read(p) for k, p in files.items()}
    return r, out


class InvokePlanReviewTest(unittest.TestCase):
    """aws answers with responses[0], responses[1], ...; payloads are recorded."""

    def review(self, responses, attempts=3):
        with tempfile.TemporaryDirectory() as tmp:
            for i, resp in enumerate(responses):
                with open(os.path.join(tmp, f"resp{i}"), "w") as f:
                    f.write(resp if isinstance(resp, str) else json.dumps(resp))
            fake_bin(tmp, "curl", 'echo \'{"value": "tok"}\'\n')
            # Last arg is the outfile; the payload follows --payload.
            fake_bin(tmp, "aws", f'''
n=$(cat "{tmp}/count" 2>/dev/null || echo 0); echo $((n + 1)) > "{tmp}/count"
while [ "$#" -gt 1 ]; do [ "$1" = "--payload" ] && echo "$2" >> "{tmp}/payloads"; shift; done
[ -f "{tmp}/resp$n" ] || exit 255
[ "$(cat "{tmp}/resp$n")" = "INVOKE_ERROR" ] && exit 255
cp "{tmp}/resp$n" "$1"
''')
            r, out = run("invoke-plan-review.sh", [PLAN], tmp, env={
                "ACTIONS_ID_TOKEN_REQUEST_URL": "http://x/?a=b", "ACTIONS_ID_TOKEN_REQUEST_TOKEN": "t",
                "REVIEW_INVOKE_ATTEMPTS": str(attempts), "REVIEW_RETRY_DELAY": "0", "NOTIFY": "true"})
            self.assertEqual(r.returncode, 0, r.stderr)
            payloads = [json.loads(line) for line in read(os.path.join(tmp, "payloads")).splitlines()]
            return r.stdout, out["output"], payloads, read(os.path.join(tmp, "review-output.txt"))

    def test_transient_failure_is_retried_until_a_verdict(self):
        stdout, output, payloads, _ = self.review(
            ["INVOKE_ERROR", {"errorMessage": "Task timed out"}, {"risk": "LOW", "markdown": "ok"}])
        self.assertIn("risk_level=LOW", output)
        self.assertEqual(len(payloads), 3)
        self.assertIn("Retrying", stdout)
        self.assertNotIn("::error", stdout)

    def test_only_the_last_attempt_lets_the_lambda_alert_on_failed(self):
        _, _, payloads, _ = self.review([{"risk": "FAILED", "markdown": "x"}] * 3)
        self.assertEqual([p["retrying"] for p in payloads], [True, True, False])
        self.assertTrue(all(p["notify"] for p in payloads))

    def test_persistent_failure_says_service_failed_not_risk(self):
        stdout, output, payloads, md = self.review(["INVOKE_ERROR"] * 2, attempts=2)
        self.assertIn("risk_level=FAILED", output)
        self.assertEqual(len(payloads), 2)
        self.assertIn("title=Plan review service failed", stdout)
        self.assertIn("not a risk finding", stdout)
        self.assertNotIn("found HIGH risk", stdout)
        self.assertIn("could not invoke", md)

    def test_high_is_final_and_says_high_risk(self):
        stdout, output, payloads, md = self.review([{"risk": "HIGH", "markdown": "Deletes the database"}])
        self.assertIn("risk_level=HIGH", output)
        self.assertEqual(len(payloads), 1)
        self.assertIn("title=Plan review found HIGH risk", stdout)
        self.assertNotIn("service failed", stdout)
        self.assertIn("Deletes the database", md)

    def test_refusal_is_not_retried(self):
        _, output, payloads, _ = self.review([{"error": "Plan 'x' does not belong to cupcake"}] * 3)
        self.assertIn("risk_level=FAILED", output)
        self.assertEqual(len(payloads), 1)


class OverrideHelpTest(unittest.TestCase):
    def help(self, risk):
        with tempfile.TemporaryDirectory() as tmp:
            return run("override-help.sh", [risk, PLAN], tmp, check=True)

    def test_high_asks_for_override_then_rerun_failed_jobs(self):
        r, out = self.help("HIGH")
        self.assertIn("found HIGH risk", r.stdout)
        self.assertIn("gh workflow run approve-override.yml -R javaBin/platform -f plan_key=" + PLAN, r.stdout)
        self.assertIn('click "Re-run failed jobs"', out["summary"])
        self.assertIn("https://github.com/javaBin/frosting/actions/runs/123", out["summary"])

    def test_failed_says_service_failed_and_rerun_all_jobs(self):
        r, out = self.help("FAILED")
        self.assertIn("service failed", r.stdout)
        self.assertIn("not a risk finding", r.stdout)
        self.assertIn('"Re-run all jobs"', out["summary"])

    def test_other_risks_print_nothing(self):
        r, out = self.help("UNKNOWN")
        self.assertEqual(r.stdout, "")
        self.assertEqual(out["summary"], "")


class OverrideTargetTest(unittest.TestCase):
    def target(self, cmd, env):
        with tempfile.TemporaryDirectory() as tmp:
            fake_bin(tmp, "gh", f'echo "$@" > "{tmp}/gh-args"\n')
            r, out = run("override-target.sh", [cmd], tmp, env=dict({"GITHUB_REPOSITORY": "javaBin/platform"}, **env))
            gh = os.path.join(tmp, "gh-args")
            out["gh"] = read(gh) if os.path.exists(gh) else None
            return r, out

    def test_resolve_reads_repo_and_run_from_plan_key(self):
        r, out = self.target("resolve", {"PLAN_KEY": PLAN, "REPO": "", "RUN_ID": ""})
        self.assertEqual(r.returncode, 0, r.stdout)
        self.assertIn("repo=javaBin/frosting\n", out["output"])
        self.assertIn("run_id=123\n", out["output"])
        self.assertIn("run_url=https://github.com/javaBin/frosting/actions/runs/123\n", out["output"])

    def test_resolve_refuses_mismatched_inputs_and_bad_keys(self):
        for env in ({"PLAN_KEY": PLAN, "REPO": "javaBin/cupcake"},
                    {"PLAN_KEY": PLAN, "RUN_ID": "999"},
                    {"PLAN_KEY": "plans/javaBin/frosting/../x/tfplan"},
                    {"PLAN_KEY": "javaBin/frosting/123/tfplan"}):
            r, out = self.target("resolve", env)
            self.assertEqual(r.returncode, 1, env)
            self.assertEqual(out["output"], "")

    def test_retrigger_reruns_platform_runs(self):
        r, out = self.target("retrigger", {"REPO": "javaBin/platform", "RUN_ID": "77"})
        self.assertEqual(r.returncode, 0)
        self.assertEqual(out["gh"], "run rerun 77 --repo javaBin/platform --failed\n")

    def test_retrigger_tells_app_developer_to_rerun(self):
        r, out = self.target("retrigger", {"REPO": "javaBin/frosting", "RUN_ID": "123"})
        self.assertEqual(r.returncode, 0)
        self.assertIsNone(out["gh"])  # github.token cannot touch app repos
        self.assertIn('open https://github.com/javaBin/frosting/actions/runs/123 and click "Re-run failed jobs"',
                      out["summary"])


if __name__ == "__main__":
    unittest.main()
