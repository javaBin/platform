"""Every platform Lambda must have a Terraform-managed log group with retention.

Lambda auto-creates /aws/lambda/<name> with "Never expire" if the group is not
declared, so a new function added anywhere under terraform/platform without a
matching log group (local.platform_lambda_names in lambdas/log-groups.tf, or an
explicit aws_cloudwatch_log_group in its own module) would log forever.

Run: python3 -m unittest discover -s scripts/tests
"""

import glob
import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "terraform", "platform")


def _read(*parts):
    with open(os.path.join(ROOT, *parts)) as f:
        return f.read()


def _toset_names(block):
    return set(re.findall(r'^\s*"([a-z0-9-]+)",\s*$', block, flags=re.M))


def _platform_tf_sources():
    for path in sorted(glob.glob(os.path.join(ROOT, "**", "*.tf"), recursive=True)):
        with open(path) as f:
            yield path, f.read()


FUNCTION_NAME = re.compile(r'function_name\s*=\s*"\$\{var\.project\}-([a-z0-9-]+)"')
EXPLICIT_LOG_GROUP = re.compile(r'name\s*=\s*"/aws/lambda/\$\{var\.project\}-([a-z0-9-]+)"')


def lambda_function_names():
    """Short names of every Lambda declared under terraform/platform."""
    names = set()
    for _, src in _platform_tf_sources():
        names |= set(FUNCTION_NAME.findall(src))
    return names


def shared_log_group_names():
    """Names in local.platform_lambda_names (the for_each in lambdas/log-groups.tf)."""
    src = _read("lambdas", "log-groups.tf")
    block = re.search(r"platform_lambda_names\s*=\s*toset\(\[(.*?)\]\)", src, flags=re.S).group(1)
    return _toset_names(block)


def log_group_names():
    """Shared for_each names plus explicit /aws/lambda/${var.project}-<name> groups in any module."""
    names = shared_log_group_names()
    for _, src in _platform_tf_sources():
        names |= set(EXPLICIT_LOG_GROUP.findall(src))
    return names


def imported_names():
    src = _read("imports.tf")
    block = re.search(
        r"import\s*\{\s*for_each\s*=\s*toset\(\[(.*?)\]\)\s*to\s*=\s*module\.lambdas\.aws_cloudwatch_log_group\.lambda",
        src,
        flags=re.S,
    ).group(1)
    return _toset_names(block)


class LambdaLogGroupTests(unittest.TestCase):
    def test_every_function_has_a_log_group(self):
        functions = lambda_function_names()
        self.assertTrue(functions, "no aws_lambda_function found; did the naming pattern change?")
        self.assertIn("auth-claims", functions, "identity module Lambda not discovered; scan is too narrow")
        missing = functions - log_group_names()
        self.assertFalse(
            missing,
            "declare a log group with retention (local.platform_lambda_names in lambdas/log-groups.tf, "
            f"or an aws_cloudwatch_log_group in the function's module): {sorted(missing)}",
        )

    def test_no_log_group_without_function(self):
        stale = log_group_names() - lambda_function_names()
        self.assertFalse(stale, f"log groups for functions that no longer exist: {sorted(stale)}")

    def test_imports_only_reference_declared_groups(self):
        unknown = imported_names() - shared_log_group_names()
        self.assertFalse(unknown, f"import blocks for undeclared log groups: {sorted(unknown)}")


if __name__ == "__main__":
    unittest.main()
