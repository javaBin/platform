"""GitHub App authentication and team resolution.

Shared by ci_broker and apply_gate Lambdas. Uses the platform GitHub App
to generate installation tokens and resolve repo→team membership.
"""

import base64
import json
import logging
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

import boto3

logger = logging.getLogger(__name__)

ssm = boto3.client("ssm")

GITHUB_ORG = os.environ.get("GITHUB_ORG", "javaBin")
GITHUB_APP_ID_PARAM = os.environ.get(
    "GITHUB_APP_ID_PARAM", "/javabin/platform/github-app-id"
)
GITHUB_APP_KEY_PARAM = os.environ.get(
    "GITHUB_APP_KEY_PARAM", "/javabin/platform/github-app-key"
)

EXCLUDED_TEAMS = {"platform"}

# Cache across invocations
_token_cache = {"token": None, "expires_at": 0}
_ssm_cache = {}


def _get_ssm(param_name):
    if param_name not in _ssm_cache:
        resp = ssm.get_parameter(Name=param_name, WithDecryption=True)
        _ssm_cache[param_name] = resp["Parameter"]["Value"]
    return _ssm_cache[param_name]


def github_app_token():
    """Generate a GitHub App installation token (cached for 50 minutes)."""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now:
        return _token_cache["token"]

    app_id = _get_ssm(GITHUB_APP_ID_PARAM)
    private_key = _get_ssm(GITHUB_APP_KEY_PARAM)

    # Build JWT (Header.Payload.Signature)
    header = base64.urlsafe_b64encode(json.dumps(
        {"alg": "RS256", "typ": "JWT"}).encode()).rstrip(b"=").decode()

    iat = int(now) - 60
    exp = iat + 600
    payload = base64.urlsafe_b64encode(json.dumps(
        {"iss": app_id, "iat": iat, "exp": exp}).encode()).rstrip(b"=").decode()

    signing_input = f"{header}.{payload}"

    # Sign with RS256 using openssl (available in Lambda runtime)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=False) as f:
        f.write(private_key)
        key_file = f.name

    result = subprocess.run(
        ["openssl", "dgst", "-sha256", "-sign", key_file],
        input=signing_input.encode(),
        capture_output=True,
    )
    os.unlink(key_file)

    if result.returncode != 0:
        raise RuntimeError(f"JWT signing failed: {result.stderr.decode()}")

    signature = base64.urlsafe_b64encode(result.stdout).rstrip(b"=").decode()
    jwt_token = f"{signing_input}.{signature}"

    # Exchange JWT for installation token
    req = urllib.request.Request(
        "https://api.github.com/app/installations",
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        installations = json.loads(resp.read())

    install_id = None
    for inst in installations:
        if inst.get("account", {}).get("login") == GITHUB_ORG:
            install_id = inst["id"]
            break

    if not install_id:
        raise RuntimeError(f"No GitHub App installation found for {GITHUB_ORG}")

    req = urllib.request.Request(
        f"https://api.github.com/app/installations/{install_id}/access_tokens",
        method="POST",
        headers={
            "Authorization": f"Bearer {jwt_token}",
            "Accept": "application/vnd.github+json",
        },
    )
    with urllib.request.urlopen(req) as resp:
        token_resp = json.loads(resp.read())

    _token_cache["token"] = token_resp["token"]
    _token_cache["expires_at"] = now + 3000  # Cache for ~50 minutes
    return _token_cache["token"]


def resolve_team(repo_name, requested_team=None):
    """Resolve which team a repo belongs to via GitHub API.

    A repo can belong to several GitHub teams. When the caller names the team
    it deploys as (the `team:` field of app.yaml), that team is returned only
    if the repo really belongs to it; otherwise None. Without a requested team,
    the first team is returned.

    Returns the team slug, or None if no matching team is found.
    Excludes platform-internal teams (e.g. 'platform').
    """
    token = github_app_token()
    req = urllib.request.Request(
        f"https://api.github.com/repos/{GITHUB_ORG}/{repo_name}/teams",
        headers={
            "Authorization": f"token {token}",
            "Accept": "application/vnd.github+json",
        },
    )
    try:
        with urllib.request.urlopen(req) as resp:
            teams = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        logger.error("GitHub API error for %s: %s", repo_name, e)
        return None

    slugs = [t["slug"] for t in teams if t["slug"] not in EXCLUDED_TEAMS]
    if requested_team:
        return requested_team if requested_team in slugs else None
    return slugs[0] if slugs else None
