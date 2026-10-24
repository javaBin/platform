"""Every platform Lambda must have a Terraform-managed log group with retention.

Lambda auto-creates /aws/lambda/<name> with "Never expire" if the group is not
declared, so a new function added to terraform/platform/lambdas/main.tf without
a matching entry in local.platform_lambda_names would log forever.

Run: python3 -m unittest discover -s scripts/tests
"""

import os
import re
import unittest

ROOT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "terraform", "platform")


def _read(*parts):
    with open(os.path.join(ROOT, *parts)) as f:
        return f.read()


def _toset_names(block):
    return set(re.findall(r'^\s*"([a-z0-9-]+)",\s*$', block, flags=re.M))


def lambda_function_names():
    src = _read("lambdas", "main.tf")
    return set(re.findall(r'function_name\s*=\s*"\$\{var\.project\}-([a-z0-9-]+)"', src))


def log_group_names():
    src = _read("lambdas", "log-groups.tf")
    block = re.search(r"platform_lambda_names\s*=\s*toset\(\[(.*?)\]\)", src, flags=re.S).group(1)
    return _toset_names(block)


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
        missing = functions - log_group_names()
        self.assertFalse(missing, f"add to local.platform_lambda_names in lambdas/log-groups.tf: {sorted(missing)}")

    def test_no_log_group_without_function(self):
        stale = log_group_names() - lambda_function_names()
        self.assertFalse(stale, f"log groups for functions that no longer exist: {sorted(stale)}")

    def test_imports_only_reference_declared_groups(self):
        unknown = imported_names() - log_group_names()
        self.assertFalse(unknown, f"import blocks for undeclared log groups: {sorted(unknown)}")


if __name__ == "__main__":
    unittest.main()
