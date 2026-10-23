"""Verify GitHub Actions OIDC tokens — shared by ci_broker and apply_gate.

The broker and gate roles are assumable from any javaBin repo running a
platform workflow, and code running inside that job (e.g. a Terraform
`external` data source during tf-plan) can call the Lambdas too. So the repo
must not come from the request payload: the caller sends a GitHub OIDC token
minted for this audience, and the repo is read from its signed `repository`
claim, which the job cannot forge.

RS256 is verified with the openssl CLI (in the Lambda runtime), so no
third-party packages are needed.
"""

import base64
import json
import os
import subprocess
import tempfile
import time
import urllib.request

ISSUER = "https://token.actions.githubusercontent.com"
JWKS_URL = f"{ISSUER}/.well-known/jwks"
GITHUB_ORG = os.environ.get("GITHUB_ORG", "javaBin")
PLATFORM_WORKFLOW_PREFIX = f"{GITHUB_ORG}/platform/.github/workflows/"
CLOCK_SKEW = 60

_jwks_cache = {"keys": None, "fetched_at": 0}


class TokenError(ValueError):
    """The token is missing, malformed, expired, or not from a platform workflow."""


def _b64url_decode(s):
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


def _der_len(n):
    if n < 0x80:
        return bytes([n])
    b = n.to_bytes((n.bit_length() + 7) // 8, "big")
    return bytes([0x80 | len(b)]) + b


def _der_int(b):
    if b[0] & 0x80:
        b = b"\x00" + b
    return b"\x02" + _der_len(len(b)) + b


def _der_seq(*parts):
    body = b"".join(parts)
    return b"\x30" + _der_len(len(body)) + body


def jwk_to_pem(jwk):
    """Encode an RSA JWK (n, e) as a SubjectPublicKeyInfo PEM."""
    rsa_key = _der_seq(_der_int(_b64url_decode(jwk["n"])), _der_int(_b64url_decode(jwk["e"])))
    algorithm = _der_seq(b"\x06\x09\x2a\x86\x48\x86\xf7\x0d\x01\x01\x01", b"\x05\x00")  # rsaEncryption, NULL
    bit_string = b"\x03" + _der_len(len(rsa_key) + 1) + b"\x00" + rsa_key
    der = _der_seq(algorithm, bit_string)
    b64 = base64.b64encode(der).decode()
    lines = [b64[i:i + 64] for i in range(0, len(b64), 64)]
    return "-----BEGIN PUBLIC KEY-----\n" + "\n".join(lines) + "\n-----END PUBLIC KEY-----\n"


def _jwks(force=False):
    if force or not _jwks_cache["keys"] or time.time() - _jwks_cache["fetched_at"] > 3600:
        with urllib.request.urlopen(JWKS_URL, timeout=10) as resp:
            _jwks_cache["keys"] = {k["kid"]: k for k in json.loads(resp.read())["keys"]}
        _jwks_cache["fetched_at"] = time.time()
    return _jwks_cache["keys"]


def _verify_rs256(signing_input, signature, jwk):
    with tempfile.NamedTemporaryFile("w", suffix=".pem", delete=False) as k, \
         tempfile.NamedTemporaryFile("wb", suffix=".sig", delete=False) as sig:
        k.write(jwk_to_pem(jwk))
        sig.write(signature)
    try:
        r = subprocess.run(
            ["openssl", "dgst", "-sha256", "-verify", k.name, "-signature", sig.name],
            input=signing_input, capture_output=True,
        )
        return r.returncode == 0
    finally:
        os.unlink(k.name)
        os.unlink(sig.name)


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

    if header.get("alg") != "RS256":
        raise TokenError(f"unsupported alg {header.get('alg')!r}")
    jwks = keys if keys is not None else _jwks()
    jwk = jwks.get(header.get("kid"))
    if jwk is None and keys is None:
        jwk = _jwks(force=True).get(header.get("kid"))  # key rotation
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
