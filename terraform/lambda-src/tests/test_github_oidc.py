"""Tests for shared.github_oidc — run: python3 -m unittest discover terraform/lambda-src/tests

Signs tokens with a throwaway RSA key via the openssl CLI, so the real
verification path (JWK -> PEM -> openssl dgst -verify) is exercised.
"""

import base64
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import unittest.mock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from shared import github_oidc as oidc  # noqa: E402

NOW = 1_800_000_000
AUD = "javabin-ci-broker"


def b64url(b):
    return base64.urlsafe_b64encode(b).rstrip(b"=").decode()


@unittest.skipUnless(shutil.which("openssl"), "openssl not installed")
class GithubOidcTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = tempfile.mkdtemp()
        cls.key = os.path.join(cls.dir, "key.pem")
        subprocess.run(["openssl", "genrsa", "-out", cls.key, "2048"], check=True, capture_output=True)
        mod = subprocess.run(["openssl", "rsa", "-in", cls.key, "-noout", "-modulus"],
                             check=True, capture_output=True, text=True).stdout.strip().split("=")[1]
        cls.jwks = {"k1": {"kid": "k1", "kty": "RSA", "e": b64url((65537).to_bytes(3, "big")),
                           "n": b64url(bytes.fromhex(mod))}}

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def token(self, **overrides):
        claims = {
            "iss": oidc.ISSUER, "aud": AUD, "exp": NOW + 300, "nbf": NOW - 10,
            "repository": "javaBin/cupcake", "repository_owner": "javaBin", "actor": "someone",
            "job_workflow_ref": "javaBin/platform/.github/workflows/tf-plan.yml@refs/heads/main",
        }
        claims.update(overrides)
        signing_input = f"{b64url(json.dumps({'alg': 'RS256', 'kid': 'k1'}).encode())}.{b64url(json.dumps(claims).encode())}"
        sig = subprocess.run(["openssl", "dgst", "-sha256", "-sign", self.key],
                             input=signing_input.encode(), check=True, capture_output=True).stdout
        return f"{signing_input}.{b64url(sig)}"

    def verify(self, token, audience=AUD):
        return oidc.verify(token, audience, now=NOW, keys=self.jwks)

    def assertRejected(self, token, fragment, audience=AUD):
        with self.assertRaises(oidc.TokenError) as cm:
            self.verify(token, audience)
        self.assertIn(fragment, str(cm.exception))

    def test_valid_token_yields_repo_from_signed_claims(self):
        self.assertEqual(oidc.repo_name(self.verify(self.token())), "cupcake")

    def test_tampered_payload_fails_signature(self):
        head, _, sig = self.token().split(".")
        forged = b64url(json.dumps({"iss": oidc.ISSUER, "aud": AUD, "exp": NOW + 300,
                                    "repository": "javaBin/moresleep", "repository_owner": "javaBin",
                                    "job_workflow_ref": "javaBin/platform/.github/workflows/tf-plan.yml@refs/heads/main"}).encode())
        self.assertRejected(f"{head}.{forged}.{sig}", "bad signature")

    def test_wrong_audience_expired_and_foreign_org_are_rejected(self):
        self.assertRejected(self.token(), "wrong audience", audience="javabin-apply-gate")
        self.assertRejected(self.token(exp=NOW - 120), "expired")
        self.assertRejected(self.token(repository_owner="evil", repository="evil/cupcake"), "javaBin org")

    def test_token_from_app_repo_workflow_is_rejected(self):
        # A token minted by the app's own workflow, not a platform reusable workflow
        self.assertRejected(self.token(job_workflow_ref="javaBin/cupcake/.github/workflows/deploy.yaml@refs/heads/main"),
                            "javaBin/platform workflow")
        self.assertRejected(self.token(job_workflow_ref="javaBin/platform/.github/workflows/tf-plan.yml@refs/heads/evil"),
                            "javaBin/platform workflow")

    def test_verification_needs_no_binaries(self):
        token = self.token()
        with unittest.mock.patch("subprocess.run", side_effect=AssertionError("shelled out")):
            self.assertEqual(oidc.repo_name(self.verify(token)), "cupcake")

    def test_non_object_claims_are_a_token_error(self):
        head = b64url(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
        self.assertRejected(f"{head}.{b64url(b'[1, 2]')}.{b64url(b'x')}", "JSON objects")

    def test_missing_or_garbage_token(self):
        self.assertRejected(None, "missing")
        self.assertRejected("a.b", "missing")

    def test_require_workflow_for_override_signing(self):
        ok = self.verify(self.token(repository="javaBin/platform", environment="override-approval",
                                    job_workflow_ref="javaBin/platform/.github/workflows/approve-override.yml@refs/heads/main"))
        oidc.require_workflow(ok, "approve-override.yml", "javaBin/platform", "override-approval")

        tf_apply = self.verify(self.token(job_workflow_ref="javaBin/platform/.github/workflows/tf-apply.yml@refs/heads/main"))
        with self.assertRaises(oidc.TokenError):
            oidc.require_workflow(tf_apply, "approve-override.yml", "javaBin/platform", "override-approval")

        no_env = self.verify(self.token(repository="javaBin/platform",
                                        job_workflow_ref="javaBin/platform/.github/workflows/approve-override.yml@refs/heads/main"))
        with self.assertRaises(oidc.TokenError):
            oidc.require_workflow(no_env, "approve-override.yml", "javaBin/platform", "override-approval")


if __name__ == "__main__":
    unittest.main()
