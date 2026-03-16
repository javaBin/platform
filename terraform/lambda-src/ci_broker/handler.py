"""CI credential broker — validates repo→team membership via GitHub API
and returns temporary STS credentials for the team-scoped IAM role.

This Lambda is the enforcement point for team-based access control.
CI workflows assume a lightweight broker role (can only invoke this Lambda),
then receive team-scoped credentials if the repo belongs to a registered team.

Actions:
  - plan:   Returns credentials for javabin-ci-team-{team} (Terraform plan/apply)
  - deploy: Returns credentials for javabin-ci-deploy-{team} (Docker build/ECS deploy)
"""

import json
import logging
import os
import time
import urllib.request

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")
sts = boto3.client("sts")

ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "")
PROJECT = os.environ.get("PROJECT", "javabin")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "javaBin")
GITHUB_APP_ID_PARAM = os.environ.get("GITHUB_APP_ID_PARAM", "/javabin/platform/github-app-id")
GITHUB_APP_KEY_PARAM = os.environ.get("GITHUB_APP_KEY_PARAM", "/javabin/platform/github-app-key")

# Cache GitHub App token across invocations (valid for 1 hour)
_token_cache = {"token": None, "expires_at": 0}
_ssm_cache = {}

PLAN_DURATION = 3600   # 1 hour for plan
DEPLOY_DURATION = 900  # 15 minutes for deploy

ROLE_MAP = {
    "plan": f"{PROJECT}-ci-team-",
    "deploy": f"{PROJECT}-ci-deploy-",
}

EXCLUDED_TEAMS = {"platform-owners"}


def _get_ssm(param_name):
    if param_name not in _ssm_cache:
        resp = ssm.get_parameter(Name=param_name, WithDecryption=True)
        _ssm_cache[param_name] = resp["Parameter"]["Value"]
    return _ssm_cache[param_name]


def _github_app_token():
    """Generate a GitHub App installation token (cached for 50 minutes)."""
    now = time.time()
    if _token_cache["token"] and _token_cache["expires_at"] > now:
        return _token_cache["token"]

    import subprocess
    import tempfile

    app_id = _get_ssm(GITHUB_APP_ID_PARAM)
    private_key = _get_ssm(GITHUB_APP_KEY_PARAM)

    # Build JWT (Header.Payload.Signature)
    import base64
    import hashlib
    import hmac

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
    # First, find the installation ID for the org
    req = urllib.request.Request(
        f"https://api.github.com/app/installations",
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


def _resolve_team(repo_name):
    """Resolve which team a repo belongs to via GitHub API."""
    token = _github_app_token()
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

    for team in teams:
        if team["slug"] not in EXCLUDED_TEAMS:
            return team["slug"]
    return None


def _assume_role(role_arn, session_name, duration):
    """Assume an IAM role and return temporary credentials."""
    resp = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=session_name,
        DurationSeconds=duration,
    )
    creds = resp["Credentials"]
    return {
        "AccessKeyId": creds["AccessKeyId"],
        "SecretAccessKey": creds["SecretAccessKey"],
        "SessionToken": creds["SessionToken"],
    }


def handler(event, context):
    repo = event.get("repo", "")
    action = event.get("action", "plan")

    if not repo:
        return {"error": "repo is required", "approved": False}

    if action not in ROLE_MAP:
        return {"error": f"Invalid action: {action}. Must be plan or deploy", "approved": False}

    # Resolve team from GitHub
    team = _resolve_team(repo)
    if not team:
        logger.warning("Repo %s does not belong to any team", repo)
        return {
            "approved": False,
            "error": f"Repo '{repo}' is not in any GitHub team. "
                     f"Add it at https://github.com/orgs/{GITHUB_ORG}/teams",
        }

    # Assume the team-scoped role
    role_prefix = ROLE_MAP[action]
    role_arn = f"arn:aws:iam::{ACCOUNT_ID}:role/{role_prefix}{team}"
    duration = PLAN_DURATION if action == "plan" else DEPLOY_DURATION

    try:
        credentials = _assume_role(role_arn, f"ci-broker-{repo}", duration)
    except Exception as e:
        logger.error("Failed to assume %s: %s", role_arn, e)
        return {
            "approved": False,
            "error": f"Failed to assume role for team '{team}': {e}",
        }

    logger.info("Issued %s credentials for repo=%s team=%s role=%s", action, repo, team, role_arn)
    return {
        "approved": True,
        "team": team,
        "credentials": credentials,
    }
