"""Tests for post-plan-comment.sh — run: python3 -m unittest discover scripts/tests"""

import os
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPT = os.path.join(HERE, "..", "post-plan-comment.sh")


def post(plan_text, limit):
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


if __name__ == "__main__":
    unittest.main()
