"""Tests for post-plan-comment.sh — run: python3 -m unittest discover scripts/tests"""

import os
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "post-plan-comment.sh")


def post(plan_text, limit, alb_errors=None):
    """Run the script with a fake `gh` that copies the comment body; return it."""
    with tempfile.TemporaryDirectory() as tmp:
        bin_dir = os.path.join(tmp, "bin")
        os.mkdir(bin_dir)
        out = os.path.join(tmp, "body.md")
        with open(os.path.join(bin_dir, "gh"), "w") as f:
            f.write(f'#!/bin/sh\nwhile [ "$1" != "--body-file" ]; do shift; done\ncp "$2" "{out}"\n')
        os.chmod(os.path.join(bin_dir, "gh"), 0o755)
        plan_dir = os.path.join(tmp, "tf")
        os.mkdir(plan_dir)
        for name in ("plan-output.txt", "plan-clean.txt"):
            with open(os.path.join(plan_dir, name), "w") as f:
                f.write(plan_text)
        if alb_errors:
            with open(os.path.join(tmp, "alb-priority-errors.txt"), "w") as f:
                f.write(alb_errors)
        env = dict(os.environ, PATH=f"{bin_dir}:{os.environ['PATH']}", PR_NUMBER="1",
                   GITHUB_REPOSITORY="javaBin/platform", PLAN_COMMENT_CHARS=str(limit))
        subprocess.run(["sh", SCRIPT, os.path.join(plan_dir, "plan-output.txt"), "true"],
                       cwd=tmp, env=env, check=True, capture_output=True)
        with open(out) as f:
            return f.read()


class PostPlanCommentTest(unittest.TestCase):
    PLAN = "".join(f"  # aws_x.r{i} will be created\n" for i in range(100)) + "Plan: 100 to add, 0 to change, 0 to destroy.\n"

    def test_cut_plan_says_it_is_cut(self):
        body = post(self.PLAN, 500)
        self.assertIn(f"cut at 500 of {len(self.PLAN)} characters", body)
        self.assertNotIn("aws_x.r99", body)

    def test_plan_that_fits_has_no_cut_note(self):
        body = post(self.PLAN, 60000)
        self.assertIn("aws_x.r99", body)
        self.assertNotIn("cut at", body)

    def test_alb_priority_conflict_is_shown_above_the_plan(self):
        body = post(self.PLAN, 60000, alb_errors="- priority 100 is already used by the rule for cupcake.javazone.no\n")
        self.assertIn("ALB rule priority conflict", body)
        self.assertIn("- priority 100 is already used by the rule for cupcake.javazone.no", body)
        self.assertLess(body.index("ALB rule priority conflict"), body.index("Plan output"))

    def test_no_conflict_section_without_errors_file(self):
        self.assertNotIn("ALB rule priority conflict", post(self.PLAN, 60000))


if __name__ == "__main__":
    unittest.main()
