"""Verify GitHub Actions OIDC tokens — shared by ci_broker and apply_gate.

The broker and gate roles are assumable from any javaBin repo running a
platform workflow, and code running inside that job (e.g. a Terraform
`external` data source during tf-plan) can call the Lambdas too. So the repo
must not come from the request payload: the caller sends a GitHub OIDC token
minted for this audience, and the repo is read from its signed `repository`
claim, which the job cannot forge.

RS256 is verified in plain Python (modular exponentiation + an exact
PKCS#1 v1.5 comparison), so no third-party packages or binaries are needed.
"""

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.request

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = f"{ISSUER}/.well-known/jwks"
GITHUB_ORG = os.environ.get("GITHUB_ORG", "javaBin")
PLATFORM_WORKFLOW_PREFIX = f"{GITHUB_ORG}/platform/.github/workflows/"
CLOCK_SKEW = 60

_jwks_cache = {"keys": None, "fetched_at": 0}
REFETCH_MIN_SECONDS = 60


class TokenError(ValueError):
    """The token is missing, malformed, expired, or not from a platform workflow."""


def _b64url_decode(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


# DigestInfo prefix for SHA-256 in an EMSA-PKCS1-v1_5 block (RFC 8017 §9.2)
_SHA256_DIGEST_INFO = bytes.fromhex("3031300d060960864801650304020105000420")


def _verify_rs256(signing_input, signature, jwk):
    """RSASSA-PKCS1-v1_5 / SHA-256 verification in plain Python.

    Rebuilds the exact expected encoded message and compares it in constant
    time; no parsing of the decrypted block, so no padding-oracle style
    leniency.
    """
    n = int.from_bytes(_b64url_decode(jwk["n"]), "big")
    e = int.from_bytes(_b64url_decode(jwk["e"]), "big")
    k = (n.bit_length() + 7) // 8
    if len(signature) != k:
        return False
    em = pow(int.from_bytes(signature, "big"), e, n).to_bytes(k, "big")
    t = _SHA256_DIGEST_INFO + hashlib.sha256(signing_input).digest()
    if k < len(t) + 11:
        return False
    expected = b"\x00\x01" + b"\xff" * (k - len(t) - 3) + b"\x00" + t
    return hmac.compare_digest(em, expected)


def _jwks(force=False):
    if force or not _jwks_cache["keys"] or time.time() - _jwks_cache["fetched_at"] > 3600:
        with urllib.request.urlopen(JWKS_URL, timeout=10) as resp:
            _jwks_cache["keys"] = {k["kid"]: k for k in json.loads(resp.read())["keys"]}
        _jwks_cache["fetched_at"] = time.time()
    return _jwks_cache["keys"]


def verify(token, audience, now=None, keys=None):
    """Return the verified claims, or raise TokenError.

    Accepts tokens from any javaBin/platform workflow on main; callers that
    need a specific workflow use require_workflow() on the result.
    """
    if not token or token.count(".") != 2:
        raise TokenError("missing or malformed GitHub OIDC token")
    header_b64, payload_b64, sig_b64 = token.split(".")
    try:
        header = json.loads(_b64url_decode(header_b64))
        claims = json.loads(_b64url_decode(payload_b64))
        signature = _b64url_decode(sig_b64)
    except ValueError as e:
        raise TokenError(f"undecodable token: {e}")
    if not isinstance(header, dict) or not isinstance(claims, dict):
        raise TokenError("token header and claims must be JSON objects")

    if header.get("alg") != "RS256":
        raise TokenError(f"unsupported alg {header.get('alg')!r}")
    jwks = keys if keys is not None else _jwks()
    jwk = jwks.get(header.get("kid"))
    if jwk is None and keys is None and time.time() - _jwks_cache["fetched_at"] > REFETCH_MIN_SECONDS:
        jwk = _jwks(force=True).get(header.get("kid"))  # key rotation (rate-limited)
    if jwk is None:
        raise TokenError("unknown signing key")
    if not _verify_rs256(f"{header_b64}.{payload_b64}".encode(), signature, jwk):
        raise TokenError("bad signature")

    now = now if now is not None else time.time()
    if claims.get("iss") != ISSUER:
        raise TokenError("wrong issuer")
    aud = claims.get("aud")
    if audience not in (aud if isinstance(aud, list) else [aud]):
        raise TokenError("wrong audience")
    if claims.get("exp", 0) + CLOCK_SKEW < now or claims.get("nbf", 0) - CLOCK_SKEW > now:
        raise TokenError("token expired or not yet valid")
    if claims.get("repository_owner") != GITHUB_ORG:
        raise TokenError("token is not from the javaBin org")
    if not str(claims.get("job_workflow_ref", "")).startswith(PLATFORM_WORKFLOW_PREFIX) or \
            not str(claims.get("job_workflow_ref", "")).endswith("@refs/heads/main"):
        raise TokenError("token is not from a javaBin/platform workflow on main")
    return claims


def require_workflow(claims, workflow, repository=None, environment=None):
    """Raise TokenError unless the token came from this platform workflow
    file (e.g. "approve-override.yml"), optionally in a given repo and
    GitHub environment."""
    if claims.get("job_workflow_ref") != f"{PLATFORM_WORKFLOW_PREFIX}{workflow}@refs/heads/main":
        raise TokenError(f"token is not from {workflow}")
    if repository and claims.get("repository") != repository:
        raise TokenError(f"token is not from {repository}")
    if environment and claims.get("environment") != environment:
        raise TokenError(f"token is not from the {environment} environment")
    return claims


def repo_name(claims):
    """Repo name (without org) from verified claims."""
    return claims["repository"].split("/", 1)[1]
