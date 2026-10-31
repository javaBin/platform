"""Tests for the app pipeline shell scripts — run: python3 -m unittest discover scripts/tests

commit-generated-tf.sh, ensure-tf-boilerplate.sh and check-aws-access.sh,
run against throwaway git repositories and directories.
"""

import os
import subprocess
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
SCRIPTS = os.path.join(HERE, "..")


def script(name):
    return os.path.join(SCRIPTS, name)


def git(cwd, *args):
    return subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True).stdout


def clean_env(**extra):
    env = {k: v for k, v in os.environ.items() if not k.startswith(("GITHUB_", "ACTIONS_"))}
    env.update(GIT_CONFIG_GLOBAL=os.devnull, GIT_CONFIG_NOSYSTEM="1", **extra)
    return env


class CommitGeneratedTfTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = self.tmp.name
        self.remote = os.path.join(root, "remote.git")
        self.work = os.path.join(root, "work")
        git(root, "init", "-q", "--bare", "-b", "main", self.remote)
        git(root, "init", "-q", "-b", "main", self.work)
        for args in (("config", "user.name", "t"), ("config", "user.email", "t@example.com")):
            git(self.work, *args)
        git(self.work, "remote", "add", "origin", self.remote)
        os.mkdir(os.path.join(self.work, "terraform"))
        self.write("terraform/main.tf", "# v1\n")
        git(self.work, "add", ".")
        git(self.work, "commit", "-q", "-m", "init")
        git(self.work, "push", "-q", "origin", "HEAD:main")

    def tearDown(self):
        self.tmp.cleanup()

    def write(self, rel, text):
        with open(os.path.join(self.work, rel), "w") as f:
            f.write(text)

    def run_script(self, **env):
        return subprocess.run(["sh", script("commit-generated-tf.sh"), "terraform", "main"],
                              cwd=self.work, env=clean_env(**env), capture_output=True, text=True)

    def remote_head(self):
        return git(self.remote, "rev-parse", "main").strip()

    def test_up_to_date_does_nothing(self):
        before = self.remote_head()
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("already up to date", result.stdout)
        self.assertEqual(self.remote_head(), before)

    def test_pushes_changes(self):
        self.write("terraform/main.tf", "# v2\n")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.remote_head(), git(self.work, "rev-parse", "HEAD").strip())

    def test_pr_push_warns_that_checks_do_not_run(self):
        self.write("terraform/main.tf", "# v2\n")
        result = self.run_script(GITHUB_HEAD_REF="main")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("checks do not run on that commit", result.stdout)

    def test_fork_pr_skips_push(self):
        before = self.remote_head()
        self.write("terraform/main.tf", "# v2\n")
        result = self.run_script(FORK_PR="true")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("comes from a fork", result.stdout)
        self.assertEqual(self.remote_head(), before)

    def test_protected_branch_fails_with_fix(self):
        hook = os.path.join(self.remote, "hooks", "pre-receive")
        with open(hook, "w") as f:
            f.write("#!/bin/sh\necho 'error: GH006: Protected branch update failed for refs/heads/main.' >&2\nexit 1\n")
        os.chmod(hook, 0o755)
        self.write("terraform/main.tf", "# v2\n")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("::error title=Generated Terraform could not be pushed::main is protected", result.stdout)
        self.assertIn("through a pull request", result.stdout)

    def test_branch_moved_fails_with_reason(self):
        other = os.path.join(self.tmp.name, "other")
        git(self.tmp.name, "clone", "-q", self.remote, other)
        with open(os.path.join(other, "README"), "w") as f:
            f.write("x\n")
        git(other, "add", ".")
        git(other, "-c", "user.name=o", "-c", "user.email=o@example.com", "commit", "-q", "-m", "other")
        git(other, "push", "-q", "origin", "HEAD:main")
        self.write("terraform/main.tf", "# v2\n")
        result = self.run_script()
        self.assertEqual(result.returncode, 1)
        self.assertIn("main moved while this run generated Terraform", result.stdout)


class EnsureTfBoilerplateTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.tf = os.path.join(self.tmp.name, "terraform")
        os.mkdir(self.tf)

    def tearDown(self):
        self.tmp.cleanup()

    def run_script(self, **env):
        env = clean_env(AWS_ACCOUNT_ID="123456789012", GITHUB_REPOSITORY="javaBin/myapp", **env)
        return subprocess.run(["sh", script("ensure-tf-boilerplate.sh"), self.tf],
                              env=env, capture_output=True, text=True)

    def read(self, name):
        with open(os.path.join(self.tf, name)) as f:
            return f.read()

    def test_uses_given_team_for_state_key_and_tags(self):
        result = self.run_script(TEAM="cupcake")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('key            = "apps/cupcake/myapp/terraform.tfstate"', self.read("backend.tf"))
        self.assertIn('team        = "cupcake"', self.read("providers.tf"))

    def test_missing_team_fails_instead_of_guessing(self):
        result = self.run_script(TEAM="")
        self.assertEqual(result.returncode, 1)
        self.assertIn("No team for javaBin/myapp", result.stdout)
        self.assertFalse(os.path.exists(os.path.join(self.tf, "backend.tf")))

    def test_missing_team_is_fine_when_repo_brings_its_own_files(self):
        for name in ("backend.tf", "providers.tf"):
            with open(os.path.join(self.tf, name), "w") as f:
                f.write("# own\n")
        result = self.run_script()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.read("backend.tf"), "# own\n")


class CheckAwsAccessTest(unittest.TestCase):
    def run_script(self, **env):
        with tempfile.NamedTemporaryFile("r") as out:
            result = subprocess.run(["sh", script("check-aws-access.sh")],
                                    env=clean_env(GITHUB_OUTPUT=out.name, **env),
                                    capture_output=True, text=True, check=True)
            return out.read(), result.stdout

    def test_available_with_oidc(self):
        output, _ = self.run_script(ACTIONS_ID_TOKEN_REQUEST_URL="https://example.invalid/token")
        self.assertEqual(output, "available=true\n")

    def test_unavailable_without_oidc_emits_notice(self):
        output, stdout = self.run_script()
        self.assertEqual(output, "available=false\n")
        self.assertIn("::notice title=Terraform plan skipped::", stdout)


if __name__ == "__main__":
    unittest.main()
