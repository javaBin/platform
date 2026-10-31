"""Team provisioner — syncs teams across Google, GitHub, AWS Budgets, Cognito, and Identity Center.

Trigger: Direct Lambda invocation from javaBin/registry CI (via javabin-ci-registry OIDC role).
Event payload contains team YAML definitions. Platform CI queues sync_team_access
after apply; EventBridge invokes sync_access_members every 3 hours.

Integrations:
- Google Admin SDK: Create/sync Google Workspace groups (domain-wide delegation SA)
- GitHub API: Create/sync GitHub teams (GitHub App installation token)
- AWS Budgets: Create team-level budget scoped to `team` tag
- Cognito: Create/sync groups in internal user pool, assign members by email
- IAM Identity Center: Create/sync groups in identity store, assign members by email.
  Users come only from Google SCIM, which provisions direct members of
  AWS_ACCESS_GROUP (javabin-developer@java.no): team and kodesmia members are added there
- Atlassian: Sync Confluence groups from registry groups (atlassian.py, report-only unless enabled)
"""

import base64
import json
import logging
import os
import subprocess
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import atlassian
import boto3
from shared.constants import USD_TO_NOK
from shared.slack import get_webhook_url, post_to_slack

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")
budgets_client = boto3.client("budgets")
cognito_client = boto3.client("cognito-idp")
dynamodb_client = boto3.client("dynamodb")
identitystore_client = boto3.client("identitystore")
iam_client = boto3.client("iam")

GOOGLE_SA_PARAM = os.environ.get(
    "GOOGLE_SA_PARAM", "/javabin/platform/google-admin-sa"
)
GOOGLE_ADMIN_EMAIL_PARAM = os.environ.get(
    "GOOGLE_ADMIN_EMAIL_PARAM", "/javabin/platform/google-admin-email"
)
GITHUB_APP_ID_PARAM = os.environ.get(
    "GITHUB_APP_ID_PARAM", "/javabin/platform/github-app-id"
)
GITHUB_APP_KEY_PARAM = os.environ.get(
    "GITHUB_APP_KEY_PARAM", "/javabin/platform/github-app-key"
)
INFRA_WEBHOOK_PARAM = os.environ.get(
    "INFRA_WEBHOOK_PARAM", "/javabin/slack/platform-resource-alerts-webhook"
)
ATLASSIAN_ORG_ID_PARAM = os.environ.get(
    "ATLASSIAN_ORG_ID_PARAM", "/javabin/platform/atlassian-org-id"
)
ATLASSIAN_API_KEY_PARAM = os.environ.get(
    "ATLASSIAN_API_KEY_PARAM", "/javabin/platform/atlassian-api-key"
)
# Report-only until set to "true" in Terraform — the event can't turn writes on.
ATLASSIAN_SYNC_APPLY = os.environ.get("ATLASSIAN_SYNC_APPLY", "false") == "true"
ACCOUNT_ID = os.environ.get("ACCOUNT_ID", "")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "javaBin")
ALERTS_TOPIC_ARN = os.environ.get("ALERTS_TOPIC_ARN", "")
COGNITO_INTERNAL_POOL_ID = os.environ.get("COGNITO_INTERNAL_POOL_ID", "")
IDENTITY_STORE_ID = os.environ.get("IDENTITY_STORE_ID", "")
SSO_INSTANCE_ARN = os.environ.get("SSO_INSTANCE_ARN", "")
PROJECT = os.environ.get("PROJECT", "javabin")
SIGNING_KEY_PARAM = os.environ.get(
    "SIGNING_KEY_PARAM", "/javabin/platform/password-token-signing-key"
)
PASSWORD_SET_URL_PARAM = os.environ.get(
    "PASSWORD_SET_URL_PARAM", "/javabin/platform/password-set-function-url"
)
BUDGET_ENFORCEMENT_TOPIC_ARN = os.environ.get("BUDGET_ENFORCEMENT_TOPIC_ARN", "")

# Parallelism for per-group / per-hero Google Admin SDK calls in handle_sync_groups_and_heros.
# Google Directory API per-user quota: 1,500 queries / 100 s (~15 QPS sustained).
# We impersonate a single admin, so 5 workers × ~3 calls/s each ≈ 15 QPS — at the budget ceiling.
GROUP_SYNC_WORKERS = int(os.environ.get("GROUP_SYNC_WORKERS", "5"))

# Team name constraints: lowercase letters only (a-z), max 12 characters.
# This keeps resource names within AWS limits (ALB TG: 32 chars for {team}-{service}).
import re
_TEAM_NAME_RE = re.compile(r"^[a-z]{1,12}$")


def _validate_team_name(name):
    """Return True if the team name meets naming constraints."""
    if not _TEAM_NAME_RE.match(name):
        logger.warning(
            "Invalid team name '%s' — must be 1-12 lowercase letters (a-z), no hyphens/digits/symbols",
            name,
        )
        return False
    return True


# javaBin horizontal white logo PNG — loaded from the Lambda zip at cold start.
# CID-embedded in welcome emails to avoid "trust this email" prompts in Outlook.
# Source file: terraform/lambda-src/team_provisioner/javabin-logo-white.png
_JAVABIN_LOGO_PNG = None


def _load_logo():
    global _JAVABIN_LOGO_PNG
    if _JAVABIN_LOGO_PNG is not None:
        return _JAVABIN_LOGO_PNG
    try:
        logo_path = os.path.join(os.path.dirname(__file__), "assets", "javabin-logo-white.b64")
        with open(logo_path) as f:
            _JAVABIN_LOGO_PNG = base64.b64decode(f.read())
    except Exception as e:
        logger.warning("Failed to load logo from zip: %s", e)
        _JAVABIN_LOGO_PNG = b""
    return _JAVABIN_LOGO_PNG

sso_client = boto3.client("sso-admin")

# Cache credentials across invocations within the same Lambda container
_credential_cache = {}


def _get_ssm_param(param_name):
    """Fetch and cache an SSM parameter value."""
    if param_name not in _credential_cache:
        resp = ssm.get_parameter(Name=param_name, WithDecryption=True)
        _credential_cache[param_name] = resp["Parameter"]["Value"]
    return _credential_cache[param_name]


# ---------------------------------------------------------------------------
# JWT / RS256 helpers (stdlib only — uses openssl CLI for RSA signing)
# ---------------------------------------------------------------------------

def _b64url(data):
    """Base64url-encode without padding."""
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign_rs256(message_bytes, private_key_pem):
    """Sign with RS256 using the openssl binary (available in Lambda runtime)."""
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".pem", delete=True
    ) as key_file:
        key_file.write(private_key_pem)
        key_file.flush()
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_file.name],
            input=message_bytes,
            capture_output=True,
        )
    if result.returncode != 0:
        raise RuntimeError(f"openssl signing failed: {result.stderr.decode()}")
    return result.stdout


def _create_jwt(payload, private_key_pem):
    """Create a signed RS256 JWT from a dict payload."""
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}))
    body = _b64url(json.dumps(payload))
    signing_input = f"{header}.{body}".encode("ascii")
    signature = _b64url(_sign_rs256(signing_input, private_key_pem))
    return f"{header}.{body}.{signature}"


# ---------------------------------------------------------------------------
# Google Admin SDK — domain-wide delegation
# ---------------------------------------------------------------------------

GOOGLE_SCOPES = (
    "https://www.googleapis.com/auth/admin.directory.group "
    "https://www.googleapis.com/auth/admin.directory.group.member "
    "https://www.googleapis.com/auth/admin.directory.user "
    "https://www.googleapis.com/auth/admin.directory.user.alias "
    "https://www.googleapis.com/auth/gmail.send "
    "https://www.googleapis.com/auth/gmail.settings.sharing"
)


GOOGLE_GROUPS_SETTINGS_SCOPE = "https://www.googleapis.com/auth/apps.groups.settings"


class GoogleScopeNotDelegated(Exception):
    """Raised when the delegated service account lacks a requested scope."""

    def __init__(self, scope):
        super().__init__(f"scope not delegated to DWD client: {scope}")
        self.scope = scope


# How long a "scope not delegated" answer is remembered, so one invocation does
# not retry (and log) the token exchange for every team.
SCOPE_NOT_DELEGATED_TTL = 300


def _get_delegated_token(scope, cache_key):
    """Exchange a domain-wide delegation JWT for a Google OAuth2 access token.

    Token is cached for ~58 minutes (Google tokens last 1 hour). Raises
    GoogleScopeNotDelegated when Google answers `unauthorized_client`, which
    happens when any requested scope is missing from the delegation grant.
    That outcome is cached for SCOPE_NOT_DELEGATED_TTL seconds.
    """
    cached = _credential_cache.get(cache_key)
    if cached and cached.get("not_delegated"):
        if cached["exp"] > time.time():
            raise GoogleScopeNotDelegated(scope)
    elif cached and cached["exp"] > time.time() + 120:
        return cached["token"]

    sa_json = json.loads(_get_ssm_param(GOOGLE_SA_PARAM))
    admin_email = _get_ssm_param(GOOGLE_ADMIN_EMAIL_PARAM)

    now = int(time.time())
    signed_jwt = _create_jwt(
        {
            "iss": sa_json["client_email"],
            "sub": admin_email,
            "scope": scope,
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        },
        sa_json["private_key"],
    )

    data = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": signed_jwt,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            token = json.loads(resp.read())["access_token"]
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        if "unauthorized_client" in body_text:
            logger.warning("Google scope not delegated (%s): %d %s", scope, e.code, body_text)
            _credential_cache[cache_key] = {
                "not_delegated": True,
                "exp": time.time() + SCOPE_NOT_DELEGATED_TTL,
            }
            raise GoogleScopeNotDelegated(scope) from e
        logger.error("Google OAuth token exchange failed: %d %s", e.code, body_text)
        raise

    _credential_cache[cache_key] = {
        "token": token,
        "exp": time.time() + 3500,
    }
    return token


def _get_google_access_token():
    """Access token for the Admin Directory + Gmail scopes (GOOGLE_SCOPES)."""
    return _get_delegated_token(GOOGLE_SCOPES, "_google_token")


def _get_google_groups_settings_token():
    """Access token for the Groups Settings API only.

    Requested on its own token so a missing delegation for this scope does not
    break the Directory sync that shares GOOGLE_SCOPES (Google rejects the
    whole token if any one requested scope is not delegated).
    """
    return _get_delegated_token(GOOGLE_GROUPS_SETTINGS_SCOPE, "_google_groups_settings_token")


def _google_api(method, path, access_token, body=None):
    """Call the Google Admin Directory API. Returns parsed JSON or None."""
    url = f"https://admin.googleapis.com/admin/directory/v1{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 409:
            return {"already_exists": True}
        if e.code == 404:
            return None
        body_text = e.read().decode("utf-8", errors="replace")
        logger.error("Google API %s %s → %d: %s", method, path, e.code, body_text)
        raise


def _get_user_google_access_token(user_email):
    """Get a Google access token impersonating a specific user (not the admin).

    Required for Gmail per-user settings (forwarding, filters, etc.).
    Tokens are cached per user for ~58 minutes.
    """
    cache_key = f"_google_token_{user_email}"
    cached = _credential_cache.get(cache_key)
    if cached and cached["exp"] > time.time() + 120:
        return cached["token"]

    sa_json = json.loads(_get_ssm_param(GOOGLE_SA_PARAM))

    now = int(time.time())
    signed_jwt = _create_jwt(
        {
            "iss": sa_json["client_email"],
            "sub": user_email,
            "scope": "https://www.googleapis.com/auth/gmail.settings.sharing",
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        },
        sa_json["private_key"],
    )

    data = urllib.parse.urlencode(
        {
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
            "assertion": signed_jwt,
        }
    ).encode("utf-8")

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req) as resp:
            token = json.loads(resp.read())["access_token"]
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        logger.error("Google OAuth token exchange (user=%s) failed: %d %s", user_email, e.code, body_text)
        raise

    _credential_cache[cache_key] = {"token": token, "exp": time.time() + 3500}
    return token


def _setup_email_forwarding(user_email, forward_to):
    """Set up auto-forwarding on a java.no account to the member's personal email.

    Uses domain-wide delegation to impersonate the user. With delegation the
    forwarding address is created with verificationStatus=accepted (no
    confirmation email required).
    """
    user_token = _get_user_google_access_token(user_email)
    gmail_base = "https://gmail.googleapis.com/gmail/v1/users/me/settings"

    # Step 1: Register the forwarding address
    fwd_body = json.dumps({"forwardingEmail": forward_to}).encode("utf-8")
    fwd_req = urllib.request.Request(
        f"{gmail_base}/forwardingAddresses",
        data=fwd_body,
        method="POST",
    )
    fwd_req.add_header("Authorization", f"Bearer {user_token}")
    fwd_req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(fwd_req) as resp:
            fwd_result = json.loads(resp.read())
            status = fwd_result.get("verificationStatus", "unknown")
            if status != "accepted":
                logger.warning("Forwarding address %s for %s has status: %s", forward_to, user_email, status)
                return False
    except urllib.error.HTTPError as e:
        if e.code == 409:
            logger.info("Forwarding address %s already registered for %s", forward_to, user_email)
        else:
            body_text = e.read().decode("utf-8", errors="replace")
            logger.error("Failed to create forwarding address %s for %s: %d %s", forward_to, user_email, e.code, body_text)
            return False

    # Step 2: Enable auto-forwarding (keep copy in inbox)
    auto_body = json.dumps({
        "enabled": True,
        "emailAddress": forward_to,
        "disposition": "leaveInInbox",
    }).encode("utf-8")
    auto_req = urllib.request.Request(
        f"{gmail_base}/autoForwarding",
        data=auto_body,
        method="PUT",
    )
    auto_req.add_header("Authorization", f"Bearer {user_token}")
    auto_req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(auto_req) as resp:
            resp.read()
            logger.info("Auto-forwarding enabled: %s → %s", user_email, forward_to)
            return True
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        logger.error("Failed to enable auto-forwarding %s → %s: %d %s", user_email, forward_to, e.code, body_text)
        return False


def _send_welcome_email(access_token, javabin_email, personal_email, firstname, password_set_url=None):
    """Send a welcome email to the hero's personal address via Gmail API.

    Uses domain-wide delegation to send as the admin email. If password_set_url
    is provided, the email includes a "Set your password" button. Otherwise falls
    back to forgot-password instructions.

    The logo is CID-embedded as an inline attachment to avoid "trust this email"
    prompts in Outlook and other clients that block external images.
    """
    admin_email = _get_ssm_param(GOOGLE_ADMIN_EMAIL_PARAM)

    subject = "Velkommen til JavaBin"

    if password_set_url:
        password_section = f"""\
            <p style="color:#333;font-size:16px;line-height:1.6;">
              Klikk p\u00e5 knappen under for \u00e5 sette passordet ditt. Lenken kan kun brukes \u00e9n gang.
            </p>
            <table cellpadding="0" cellspacing="0" style="margin:24px 0;">
              <tr><td align="center" style="background-color:#f05350;border-radius:6px;">
                <a href="{password_set_url}" style="display:inline-block;padding:14px 32px;color:#ffffff;font-size:16px;font-weight:600;text-decoration:none;">
                  Sett passord
                </a>
              </td></tr>
            </table>"""
    else:
        password_section = f"""\
            <p style="color:#333;font-size:16px;line-height:1.6;"><strong>Slik setter du passord:</strong></p>
            <ol style="color:#333;font-size:15px;line-height:1.8;padding-left:20px;">
              <li>G\u00e5 til <strong>accounts.google.com</strong></li>
              <li>Skriv inn <strong>{javabin_email}</strong></li>
              <li>Trykk p\u00e5 \u00abGlemt passord\u00bb</li>
              <li>Du f\u00e5r en lenke til <strong>{personal_email}</strong></li>
            </ol>"""

    body_html = f"""\
<html>
<body style="margin:0;padding:0;background-color:#f5f5f5;font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,sans-serif;">
  <table width="100%" cellpadding="0" cellspacing="0" style="background-color:#f5f5f5;padding:40px 0;">
    <tr><td align="center">
      <table width="600" cellpadding="0" cellspacing="0" style="background-color:#ffffff;border-radius:8px;overflow:hidden;">
        <!-- Header -->
        <tr>
          <td style="background-color:#f05350;padding:30px 40px;text-align:center;">
            <img src="cid:javabin-logo" alt="javaBin" width="200" style="display:inline-block;" />
          </td>
        </tr>
        <!-- Body -->
        <tr>
          <td style="padding:40px;">
            <h1 style="color:#f05350;font-size:22px;margin:0 0 20px;">Hei {firstname}!</h1>
            <p style="color:#333;font-size:16px;line-height:1.6;">
              Velkommen til JavaBin! Din Google Workspace-konto er n\u00e5 opprettet.
            </p>
            <table style="background-color:#fef2f2;border-radius:6px;padding:20px;margin:20px 0;width:100%;">
              <tr><td>
                <p style="margin:0;color:#666;font-size:13px;text-transform:uppercase;letter-spacing:1px;">Brukernavn</p>
                <p style="margin:4px 0 0;color:#f05350;font-size:18px;font-weight:600;">{javabin_email}</p>
              </td></tr>
            </table>
{password_section}
            <p style="color:#333;font-size:16px;line-height:1.6;">
              N\u00e5r du er logget inn har du tilgang til javaBin sine tjenester, e-post og delte dokumenter.
            </p>
          </td>
        </tr>
        <!-- Footer -->
        <tr>
          <td style="background-color:#f8f9fa;padding:20px 40px;border-top:1px solid #e9ecef;">
            <p style="margin:0;color:#999;font-size:13px;text-align:center;">
              JavaBin \u2014 Javabrukerforeningen i Norge
              <br/><a href="https://java.no" style="color:#666;text-decoration:none;">java.no</a>
              &nbsp;\u00b7&nbsp;
              <a href="https://javazone.no" style="color:#666;text-decoration:none;">javazone.no</a>
            </p>
          </td>
        </tr>
      </table>
    </td></tr>
  </table>
</body>
</html>"""

    # Build MIME message — multipart/related with CID logo if available,
    # otherwise fall back to plain HTML with external image URL.
    import email.mime.multipart
    import email.mime.text
    import email.mime.image

    logo_data = _load_logo()
    if logo_data:
        msg = email.mime.multipart.MIMEMultipart("related")
        msg.attach(email.mime.text.MIMEText(body_html, "html", "utf-8"))
        logo_part = email.mime.image.MIMEImage(logo_data, _subtype="png")
        logo_part.add_header("Content-ID", "<javabin-logo>")
        logo_part.add_header("Content-Disposition", "inline", filename="javabin-logo.png")
        msg.attach(logo_part)
    else:
        # Fallback: replace cid: reference with external URL
        fallback_url = "https://java.no/_next/static/media/javaBin-logo-horizontal-WHITE.434dba55.png"
        body_html = body_html.replace('src="cid:javabin-logo"', f'src="{fallback_url}"')
        msg = email.mime.text.MIMEText(body_html, "html", "utf-8")

    msg["To"] = personal_email
    msg["From"] = admin_email
    msg["Subject"] = subject
    raw_message = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")

    # Send via Gmail API (impersonating admin)
    gmail_url = f"https://gmail.googleapis.com/gmail/v1/users/me/messages/send"
    gmail_body = json.dumps({"raw": raw_message}).encode("utf-8")
    req = urllib.request.Request(gmail_url, data=gmail_body, method="POST")
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            logger.info("Sent welcome email to %s for account %s", personal_email, javabin_email)
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        logger.error("Gmail send failed: %d %s", e.code, body_text)
        raise


def _normalize_member(member):
    """Normalize a member entry to a dict with email and github fields.

    Supports two formats:
    - Simple string: "alexander.amiri"
        → email: alexander.amiri@java.no (Google Workspace username)
        → github: not set (must use dict format for GitHub team sync)
    - Dict: {google: "alexander.amiri", github: "alexanderamiri"}
        → email: alexander.amiri@java.no (derived from google field)
        → github: alexanderamiri

    The 'google' field is the username part only — @java.no is always appended.
    Legacy 'email' field is also accepted for backwards compatibility.
    Members without a 'github' field are synced to Google Groups only.
    """
    if isinstance(member, str):
        username = member.strip()
        return {"email": f"{username}@java.no"}
    if isinstance(member, dict):
        result = dict(member)
        # Derive email from 'google' field if 'email' is not set
        if "email" not in result and "google" in result:
            result["email"] = f"{result['google']}@java.no"
        return result
    logger.warning("Unexpected member format: %s", member)
    return {}


# Settings enforced on every team group so members can read the group's mail.
# Who-can-post and moderation are deliberately NOT enforced: outside senders
# keep reaching the group exactly as before.
TEAM_GROUP_SETTINGS = {
    "whoCanViewGroup": "ALL_MEMBERS_CAN_VIEW",
    "isArchived": "true",
    "archiveOnly": "false",
    "whoCanViewMembership": "ALL_IN_DOMAIN_CAN_VIEW",
    "whoCanJoin": "INVITED_CAN_JOIN",
    "allowWebPosting": "true",
    "allowExternalMembers": "false",
}

# Settings reported (never written) so the first run shows today's values.
TEAM_GROUP_REPORTED_SETTINGS = ("whoCanPostMessage", "messageModerationLevel")

GOOGLE_MEMBER_ROLE_FOR = {"maintainer": "MANAGER", "member": "MEMBER"}


class GoogleGroupsSettingsApiDisabled(Exception):
    """Raised when the Groups Settings API is not enabled in the GCP project."""


def _groups_settings_api(method, email, access_token, body=None):
    """Call the Google Groups Settings API. Returns parsed JSON, or None on 404."""
    url = (
        "https://www.googleapis.com/groups/v1/groups/"
        f"{urllib.parse.quote(email, safe='')}?alt=json"
    )
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {access_token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 404:
            return None
        body_text = e.read().decode("utf-8", errors="replace")
        logger.error("Groups Settings API %s %s -> %d: %s", method, email, e.code, body_text)
        if e.code == 403 and ("accessNotConfigured" in body_text or "SERVICE_DISABLED" in body_text):
            raise GoogleGroupsSettingsApiDisabled(body_text[:200]) from e
        raise


def enforce_team_group_settings(group_email):
    """Make sure team members can read the group's mail and archive.

    Reads the current settings and PATCHes only keys that differ, so a group
    already in sync gets no write. Never raises: returns a dict describing the
    outcome so member sync still succeeds when settings cannot be applied.
    """
    try:
        token = _get_google_groups_settings_token()
    except GoogleScopeNotDelegated:
        return {"skipped": True, "reason": "groups settings scope not delegated"}

    try:
        current = None
        # A freshly created group can 404 here for a few seconds.
        for delay in (2, 4, 8, None):
            current = _groups_settings_api("GET", group_email, token)
            if current is not None or delay is None:
                break
            time.sleep(delay)
        if current is None:
            return {"error": f"groups settings for {group_email} not found"}

        reported = {k: current.get(k) for k in TEAM_GROUP_REPORTED_SETTINGS}
        drift = {
            k: v for k, v in TEAM_GROUP_SETTINGS.items()
            if str(current.get(k, "")).lower() != v.lower()
        }
        if drift:
            _groups_settings_api("PATCH", group_email, token, drift)
            logger.info("Updated settings on %s: %s", group_email, sorted(drift))
        return {"changed": sorted(drift), "current": reported}
    except GoogleGroupsSettingsApiDisabled:
        return {
            "error": "Groups Settings API (groupssettings.googleapis.com) is not "
                     "enabled in GCP project javabin-platform"
        }
    except Exception as e:  # noqa: BLE001 — settings must not fail member sync
        logger.exception("Failed to enforce settings on %s", group_email)
        return {"error": str(e)[:200]}


def _google_role_for(member):
    """Registry role -> Google group role. maintainer -> MANAGER, else MEMBER."""
    return GOOGLE_MEMBER_ROLE_FOR.get(str(member.get("role", "member")).lower(), "MEMBER")


def sync_google_group(team):
    """Create or update a Google Workspace group and sync its membership.

    Group email is derived as team-{name}@java.no. Registry `role: maintainer`
    becomes Google MANAGER, everyone else MEMBER. OWNERs (only ever added by
    a Workspace admin by hand) are never demoted or removed, even when they are
    not in the registry. Members outside java.no are not added (the group does
    not allow external members); they are reported in `skipped_external`.
    Group settings are then enforced so members can read the mail archive (see
    enforce_team_group_settings).
    """
    team_name = team["name"]
    group_email = f"team-{team_name}@java.no"

    access_token = _get_google_access_token()
    group_key = urllib.parse.quote(group_email, safe="")

    group_body = {
        "email": group_email,
        "name": team_name,
        "description": team["description"],
    }

    existing = _google_api("GET", f"/groups/{group_key}", access_token)
    if existing and not existing.get("already_exists"):
        _google_api("PUT", f"/groups/{group_key}", access_token, group_body)
        logger.info("Updated Google group %s", group_email)
    else:
        result = _google_api("POST", "/groups", access_token, group_body)
        if result and result.get("already_exists"):
            logger.info("Google group %s already exists", group_email)
        else:
            logger.info("Created Google group %s", group_email)

    # Current members (paginated — Google returns max 200 per page)
    current_roles = {}  # email -> role
    page_token = None
    while True:
        path = f"/groups/{group_key}/members?maxResults=200"
        if page_token:
            path += f"&pageToken={urllib.parse.quote(page_token, safe='')}"
        resp = _google_api("GET", path, access_token)
        if resp and "members" in resp:
            for m in resp["members"]:
                current_roles[m["email"].lower()] = m.get("role", "MEMBER")
        if resp and resp.get("nextPageToken"):
            page_token = resp["nextPageToken"]
        else:
            break

    # Desired members
    desired_emails = set()
    added = 0
    role_changes = []
    skipped_external = []
    for member in team.get("members", []):
        m = _normalize_member(member)
        email = m.get("email", "").lower()
        if not email:
            continue
        desired_emails.add(email)
        role = _google_role_for(m)

        if email not in current_roles and not email.endswith("@java.no"):
            # allowExternalMembers=false: Google would reject the insert and
            # abort the rest of this team's sync. Report instead.
            skipped_external.append(email)
            logger.warning("Not adding external address %s to %s", email, group_email)
        elif email not in current_roles:
            _google_api(
                "POST",
                f"/groups/{group_key}/members",
                access_token,
                {"email": email, "role": role},
            )
            added += 1
            logger.info("Added %s to %s as %s", email, group_email, role)
        elif current_roles[email] != role and current_roles[email] != "OWNER":
            encoded_email = urllib.parse.quote(email, safe="")
            _google_api(
                "PATCH",
                f"/groups/{group_key}/members/{encoded_email}",
                access_token,
                {"role": role},
            )
            role_changes.append(f"{email} {current_roles[email]}->{role}")
            logger.info("Changed %s in %s: %s -> %s", email, group_email, current_roles[email], role)

    # Remove members no longer in the team definition. OWNERs are kept: they
    # are set by a Workspace admin, not by the registry.
    kept_owners = []
    for email, current_role in current_roles.items():
        if email not in desired_emails and current_role == "OWNER":
            kept_owners.append(email)
        elif email not in desired_emails:
            encoded_email = urllib.parse.quote(email, safe="")
            _google_api(
                "DELETE",
                f"/groups/{group_key}/members/{encoded_email}",
                access_token,
            )
            logger.info("Removed %s from %s", email, group_email)

    return {
        "synced": True,
        "group": group_email,
        "member_count": len(desired_emails),
        "added": added,
        "role_changes": role_changes,
        "skipped_external": skipped_external,
        "kept_owners": kept_owners,
        "settings": enforce_team_group_settings(group_email),
    }


# ---------------------------------------------------------------------------
# GitHub API — App authentication
# ---------------------------------------------------------------------------

_GITHUB_HEADERS = {
    "Accept": "application/vnd.github+json",
    "X-GitHub-Api-Version": "2022-11-28",
}


def _get_github_installation_token():
    """Create a GitHub App installation token for the javaBin org.

    Token is cached for ~9 minutes (GitHub installation tokens last 10 min).
    """
    cached = _credential_cache.get("_github_token")
    if cached and cached["exp"] > time.time() + 60:
        return cached["token"]

    app_id = _get_ssm_param(GITHUB_APP_ID_PARAM)
    app_key = _get_ssm_param(GITHUB_APP_KEY_PARAM)

    now = int(time.time())
    app_jwt = _create_jwt(
        {"iat": now - 60, "exp": now + 600, "iss": app_id}, app_key
    )

    # Resolve the org installation ID
    req = urllib.request.Request(
        f"https://api.github.com/orgs/{GITHUB_ORG}/installation",
        headers={**_GITHUB_HEADERS, "Authorization": f"Bearer {app_jwt}"},
    )
    with urllib.request.urlopen(req) as resp:
        installation_id = json.loads(resp.read())["id"]

    # Exchange for an installation access token
    req = urllib.request.Request(
        f"https://api.github.com/app/installations/{installation_id}/access_tokens",
        data=b"{}",
        method="POST",
        headers={**_GITHUB_HEADERS, "Authorization": f"Bearer {app_jwt}"},
    )
    with urllib.request.urlopen(req) as resp:
        token = json.loads(resp.read())["token"]

    _credential_cache["_github_token"] = {
        "token": token,
        "exp": time.time() + 540,
    }
    return token


def _github_api(method, path, token, body=None):
    """Call the GitHub REST API. Returns parsed JSON, empty dict, or None (404)."""
    url = f"https://api.github.com{path}"
    data = json.dumps(body).encode("utf-8") if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    for k, v in _GITHUB_HEADERS.items():
        req.add_header(k, v)
    if data:
        req.add_header("Content-Type", "application/json")

    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        if e.code == 422:
            return {"already_exists": True}
        if e.code == 404:
            return None
        body_text = e.read().decode("utf-8", errors="replace")
        logger.error("GitHub API %s %s → %d: %s", method, path, e.code, body_text)
        raise


# GitHub team role for members without a role: field. Maintainer, so every
# member can add the team's repos to it (registering a team never writes
# role:). Google keeps MEMBER as its default (_google_role_for).
GITHUB_DEFAULT_TEAM_ROLE = "maintainer"


def sync_github_team(team):
    """Create or update a GitHub team and sync its membership.

    Members get the role from their YAML entry (default: "maintainer", see
    GITHUB_DEFAULT_TEAM_ROLE). Set role: member to limit someone to the
    team's repos without managing the team.
    """
    team_name = team["name"]
    token = _get_github_installation_token()
    team_slug = team_name.lower().replace(" ", "-")

    team_body = {
        "name": team_name,
        "description": team["description"],
        "privacy": "closed",
    }

    existing = _github_api(
        "GET", f"/orgs/{GITHUB_ORG}/teams/{team_slug}", token
    )
    if existing and not existing.get("already_exists"):
        _github_api(
            "PATCH",
            f"/orgs/{GITHUB_ORG}/teams/{team_slug}",
            token,
            team_body,
        )
        logger.info("Updated GitHub team %s", team_slug)
    else:
        result = _github_api(
            "POST", f"/orgs/{GITHUB_ORG}/teams", token, team_body
        )
        if result and result.get("already_exists"):
            logger.info("GitHub team %s already exists", team_slug)
        else:
            logger.info("Created GitHub team %s", team_slug)

    # Current team members with roles (paginated — GitHub returns max 100 per page)
    # We need roles to avoid unnecessary PUT calls that can change membership state.
    current_members = {}  # login -> role
    for role_filter in ("maintainer", "member"):
        page = 1
        while True:
            members_resp = _github_api(
                "GET",
                f"/orgs/{GITHUB_ORG}/teams/{team_slug}/members?role={role_filter}&per_page=100&page={page}",
                token,
            )
            if not isinstance(members_resp, list) or not members_resp:
                break
            for m in members_resp:
                current_members[m["login"].lower()] = role_filter
            if len(members_resp) < 100:
                break
            page += 1

    # Desired members — only call PUT if role needs changing
    desired_users = set()
    for member in team.get("members", []):
        m = _normalize_member(member)
        github_user = m.get("github", "").lower()
        if not github_user:
            logger.warning(
                "No GitHub handle for member %s in team %s — skipping",
                member,
                team_name,
            )
            continue
        desired_users.add(github_user)
        github_role = m.get("role", GITHUB_DEFAULT_TEAM_ROLE)
        if github_role not in ("maintainer", "member"):
            logger.warning("Invalid role '%s' for %s — defaulting to member", github_role, github_user)
            github_role = "member"
        current_role = current_members.get(github_user)
        if current_role == github_role:
            logger.info("Skipping %s in team %s — already %s", github_user, team_slug, github_role)
            continue
        _github_api(
            "PUT",
            f"/orgs/{GITHUB_ORG}/teams/{team_slug}/memberships/{github_user}",
            token,
            {"role": github_role},
        )
        logger.info("Set %s as %s in team %s", github_user, github_role, team_slug)

    # Remove members no longer in the team definition
    for login in set(current_members.keys()) - desired_users:
        _github_api(
            "DELETE",
            f"/orgs/{GITHUB_ORG}/teams/{team_slug}/memberships/{login}",
            token,
        )
        logger.info("Removed %s from GitHub team %s", login, team_slug)

    return {
        "synced": True,
        "team": team_slug,
        "member_count": len(desired_users),
    }


# ---------------------------------------------------------------------------
# AWS Budgets — team-level budget scoped to the `team` cost allocation tag
# ---------------------------------------------------------------------------

def sync_budget(team):
    """Create or update an AWS Budget for this team."""
    budget_nok = team.get("budget_nok", 500)

    budget_usd = round(budget_nok / USD_TO_NOK, 2)
    budget_name = f"javabin-team-{team['name']}"

    budget_spec = {
        "BudgetName": budget_name,
        "BudgetLimit": {"Amount": str(budget_usd), "Unit": "USD"},
        "BudgetType": "COST",
        "TimeUnit": "MONTHLY",
        "CostFilters": {"TagKeyValue": [f"user:team${team['name']}"]},
    }

    try:
        budgets_client.describe_budget(
            AccountId=ACCOUNT_ID, BudgetName=budget_name
        )
        budgets_client.update_budget(
            AccountId=ACCOUNT_ID, NewBudget=budget_spec
        )
        logger.info("Updated budget %s: $%.2f/mo", budget_name, budget_usd)
    except budgets_client.exceptions.NotFoundException:
        notifications = [
            {
                "Notification": {
                    "NotificationType": "ACTUAL",
                    "ComparisonOperator": "GREATER_THAN",
                    "Threshold": 80.0,
                    "ThresholdType": "PERCENTAGE",
                },
                "Subscribers": [
                    {"SubscriptionType": "SNS", "Address": ALERTS_TOPIC_ARN}
                ],
            },
        ]
        # 200% enforcement notification — triggers budget-enforcer Lambda to
        # scale ECS services to zero for the team.
        if BUDGET_ENFORCEMENT_TOPIC_ARN:
            notifications.append({
                "Notification": {
                    "NotificationType": "ACTUAL",
                    "ComparisonOperator": "GREATER_THAN",
                    "Threshold": 200.0,
                    "ThresholdType": "PERCENTAGE",
                },
                "Subscribers": [
                    {"SubscriptionType": "SNS", "Address": BUDGET_ENFORCEMENT_TOPIC_ARN}
                ],
            })
        budgets_client.create_budget(
            AccountId=ACCOUNT_ID,
            Budget=budget_spec,
            NotificationsWithSubscribers=notifications,
        )
        logger.info("Created budget %s: $%.2f/mo", budget_name, budget_usd)

    return {"synced": True, "budget": budget_name, "usd": budget_usd}


# ---------------------------------------------------------------------------
# Cognito + Identity Center — stubs with clear extension points
# ---------------------------------------------------------------------------

MEMBERSHIPS_TABLE = os.environ.get("MEMBERSHIPS_TABLE", "")


def _write_membership(group_name, emails):
    """Record a group's registry members for the auth-claims trigger.

    Cognito has no membership for federated users until they sign in, so the
    pre-token trigger puts groups into tokens from this table instead.
    """
    if not MEMBERSHIPS_TABLE:
        return
    dynamodb_client.put_item(
        TableName=MEMBERSHIPS_TABLE,
        Item={
            "pk": {"S": f"group#{group_name}"},
            "members": {"L": [{"S": e.lower()} for e in sorted(set(emails)) if e]},
            "source": {"S": "registry"},
        },
    )


def _prune_memberships(current_groups):
    """Delete recorded registry groups that are gone or no longer cognito-enabled.

    team-<name> rows are owned by team sync and are left alone.
    """
    if not MEMBERSHIPS_TABLE:
        return []
    pruned, kwargs = [], {"TableName": MEMBERSHIPS_TABLE, "ProjectionExpression": "pk"}
    while True:
        page = dynamodb_client.scan(**kwargs)
        for item in page.get("Items", []):
            pk = item["pk"]["S"]
            name = pk[len("group#"):]
            if pk.startswith("group#") and not name.startswith("team-") and name not in current_groups:
                dynamodb_client.delete_item(TableName=MEMBERSHIPS_TABLE, Key={"pk": {"S": pk}})
                pruned.append(name)
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    if pruned:
        logger.info("Pruned stale auth memberships: %s", pruned)
    return pruned


def sync_cognito_group(team):
    """Create or update a Cognito group in the internal user pool and sync membership.

    Members are matched by email (username in Cognito). Members not yet in the
    pool are skipped (they'll be added on next sync after they sign up).
    """
    if not COGNITO_INTERNAL_POOL_ID:
        logger.info("Cognito sync skipped — no pool ID configured (team: %s)", team["name"])
        return {"skipped": True, "reason": "cognito_pool_not_configured"}

    team_name = team["name"]
    pool_id = COGNITO_INTERNAL_POOL_ID
    group_name = f"team-{team_name}"

    # Create or update group
    try:
        cognito_client.get_group(GroupName=group_name, UserPoolId=pool_id)
        cognito_client.update_group(
            GroupName=group_name,
            UserPoolId=pool_id,
            Description=team["description"],
        )
        logger.info("Updated Cognito group %s", group_name)
    except cognito_client.exceptions.ResourceNotFoundException:
        cognito_client.create_group(
            GroupName=group_name,
            UserPoolId=pool_id,
            Description=team["description"],
        )
        logger.info("Created Cognito group %s", group_name)

    # Current members in the group
    current_users = set()
    next_token = None
    while True:
        kwargs = {"GroupName": group_name, "UserPoolId": pool_id, "Limit": 60}
        if next_token:
            kwargs["NextToken"] = next_token
        resp = cognito_client.list_users_in_group(**kwargs)
        for user in resp.get("Users", []):
            for attr in user.get("Attributes", []):
                if attr["Name"] == "email":
                    current_users.add(attr["Value"].lower())
        next_token = resp.get("NextToken")
        if not next_token:
            break

    # Desired members — resolve email to Cognito username
    desired_emails = set()
    for member in team.get("members", []):
        m = _normalize_member(member)
        email = m.get("email", "").lower()
        if not email:
            continue
        desired_emails.add(email)

        if email not in current_users:
            # Look up user by email in the pool
            users_resp = cognito_client.list_users(
                UserPoolId=pool_id,
                Filter=f'email = "{email}"',
                Limit=1,
            )
            if users_resp.get("Users"):
                username = users_resp["Users"][0]["Username"]
                cognito_client.admin_add_user_to_group(
                    UserPoolId=pool_id,
                    Username=username,
                    GroupName=group_name,
                )
                logger.info("Added %s to Cognito group %s", email, group_name)
            else:
                logger.info("User %s not in Cognito pool — skipping", email)

    # Remove members no longer in the team
    for email in current_users - desired_emails:
        users_resp = cognito_client.list_users(
            UserPoolId=pool_id,
            Filter=f'email = "{email}"',
            Limit=1,
        )
        if users_resp.get("Users"):
            username = users_resp["Users"][0]["Username"]
            cognito_client.admin_remove_user_from_group(
                UserPoolId=pool_id,
                Username=username,
                GroupName=group_name,
            )
            logger.info("Removed %s from Cognito group %s", email, group_name)

    _write_membership(group_name, desired_emails)
    return {"synced": True, "group": group_name, "member_count": len(desired_emails)}


def sync_identity_center_group(team):
    """Create or update an Identity Center group and sync membership.

    Members are matched by email in the identity store. Members Google SCIM
    hasn't provisioned to Identity Center can't be added; they are reported
    in `not_in_identity_center` (see _not_in_identity_center).
    """
    if not IDENTITY_STORE_ID:
        logger.info(
            "Identity Center sync skipped — no store ID configured (team: %s)",
            team["name"],
        )
        return {"skipped": True, "reason": "identity_store_not_configured"}

    team_name = team["name"]
    group_name = f"team-{team_name}"
    store_id = IDENTITY_STORE_ID

    # Find or create group
    group_id = None
    groups_resp = identitystore_client.list_groups(
        IdentityStoreId=store_id,
        Filters=[{"AttributePath": "DisplayName", "AttributeValue": group_name}],
    )
    if groups_resp.get("Groups"):
        group_id = groups_resp["Groups"][0]["GroupId"]
        logger.info("Found Identity Center group %s (%s)", group_name, group_id)
    else:
        create_resp = identitystore_client.create_group(
            IdentityStoreId=store_id,
            DisplayName=group_name,
            Description=team["description"],
        )
        group_id = create_resp["GroupId"]
        logger.info("Created Identity Center group %s (%s)", group_name, group_id)

    # Current group memberships
    current_members = {}  # user_id -> membership_id
    next_token = None
    while True:
        kwargs = {"IdentityStoreId": store_id, "GroupId": group_id}
        if next_token:
            kwargs["NextToken"] = next_token
        resp = identitystore_client.list_group_memberships(**kwargs)
        for membership in resp.get("GroupMemberships", []):
            member_id = membership.get("MemberId", {}).get("UserId")
            if member_id:
                current_members[member_id] = membership["MembershipId"]
        next_token = resp.get("NextToken")
        if not next_token:
            break

    # Build reverse lookup: user_id -> email for current members
    current_emails = {}  # email -> user_id
    for user_id in current_members:
        try:
            user = identitystore_client.describe_user(
                IdentityStoreId=store_id, UserId=user_id
            )
            for email_obj in user.get("Emails", []):
                current_emails[email_obj["Value"].lower()] = user_id
        except Exception:
            logger.warning("Could not describe user %s", user_id)

    # Desired members
    desired_emails = set()
    not_provisioned = []
    for member in team.get("members", []):
        m = _normalize_member(member)
        email = m.get("email", "").lower()
        if not email:
            continue
        desired_emails.add(email)

        if email not in current_emails:
            # Look up user by email
            users_resp = identitystore_client.list_users(
                IdentityStoreId=store_id,
                Filters=[{"AttributePath": "UserName", "AttributeValue": email}],
            )
            if users_resp.get("Users"):
                user_id = users_resp["Users"][0]["UserId"]
                try:
                    identitystore_client.create_group_membership(
                        IdentityStoreId=store_id,
                        GroupId=group_id,
                        MemberId={"UserId": user_id},
                    )
                    logger.info("Added %s to Identity Center group %s", email, group_name)
                except identitystore_client.exceptions.ConflictException:
                    logger.info("User %s already in Identity Center group %s", email, group_name)
            else:
                logger.warning("User %s not in Identity Center (not SCIM-provisioned) — skipping", email)
                not_provisioned.append(email)

    # Remove members no longer in the team
    for email, user_id in current_emails.items():
        if email not in desired_emails:
            membership_id = current_members.get(user_id)
            if membership_id:
                identitystore_client.delete_group_membership(
                    IdentityStoreId=store_id,
                    MembershipId=membership_id,
                )
                logger.info("Removed %s from Identity Center group %s", email, group_name)

    # Create per-team permission set and assign to group
    ps_result = None
    if SSO_INSTANCE_ARN and ACCOUNT_ID:
        ps_result = _ensure_team_permission_set(team_name, group_id)

    result = {
        "synced": True,
        "group": group_name,
        "member_count": len(desired_emails) - len(not_provisioned),
    }
    if not_provisioned:
        result["not_in_identity_center"] = sorted(not_provisioned)
    if ps_result:
        result["permission_set"] = ps_result
    return result


# ---------------------------------------------------------------------------
# Group provisioning — create groups, sync members, assign permission sets
#
# Identity Center: SCIM syncs users from Google, but NOT group membership.
#   The provisioner reads Google group members and adds them to IC groups.
# Cognito: JIT creates users on first sign-in. Groups come via SAML claims.
#   The provisioner only creates group names, no member sync needed.
# ---------------------------------------------------------------------------

def _get_google_group_members(group_email):
    """Fetch all members of a Google Workspace group."""
    access_token = _get_google_access_token()
    group_key = urllib.parse.quote(group_email, safe="")

    members = []
    page_token = None
    while True:
        path = f"/groups/{group_key}/members?maxResults=200"
        if page_token:
            path += f"&pageToken={urllib.parse.quote(page_token, safe='')}"
        resp = _google_api("GET", path, access_token)
        if resp and "members" in resp:
            for m in resp["members"]:
                members.append(m["email"].lower())
        if resp and resp.get("nextPageToken"):
            page_token = resp["nextPageToken"]
        else:
            break

    return members


def sync_group_to_cognito(group):
    """Ensure a Cognito group exists and record its registry members.

    Members reach tokens through the auth-claims pre-token trigger, which
    reads the memberships table — Cognito group membership is not synced.
    """
    if not COGNITO_INTERNAL_POOL_ID:
        return {"skipped": True, "reason": "cognito_pool_not_configured"}
    if not group.get("cognito"):
        return {"skipped": True, "reason": "cognito_not_enabled"}

    group_name = group["name"]
    # Only payloads with resolved members (sync_groups_and_heros) carry a
    # member list; others must not wipe the recorded membership.
    if "members" in group:
        _write_membership(group_name, group["members"])
    google_email = group["google"]
    pool_id = COGNITO_INTERNAL_POOL_ID

    try:
        cognito_client.get_group(GroupName=group_name, UserPoolId=pool_id)
        logger.info("Cognito group %s exists", group_name)
    except cognito_client.exceptions.ResourceNotFoundException:
        cognito_client.create_group(
            GroupName=group_name,
            UserPoolId=pool_id,
            Description=f"{group_name} (membership via Google sign-in, source: {google_email})",
        )
        logger.info("Created Cognito group %s", group_name)

    return {"synced": True, "group": group_name}


def sync_group_to_identity_center(group):
    """Create Identity Center group, sync members from Google, assign permission set.

    SCIM only provisions users — it does NOT sync Google group membership to
    Identity Center groups. This function bridges that gap by reading the
    Google group members and adding them to the Identity Center group.
    """
    if not IDENTITY_STORE_ID:
        return {"skipped": True, "reason": "identity_store_not_configured"}
    if not group.get("identity_center"):
        return {"skipped": True, "reason": "identity_center_not_enabled"}

    group_name = group["name"]
    google_email = group["google"]
    permission_set_name = group.get("permission_set")
    store_id = IDENTITY_STORE_ID

    # Find or create group
    group_id = None
    groups_resp = identitystore_client.list_groups(
        IdentityStoreId=store_id,
        Filters=[{"AttributePath": "DisplayName", "AttributeValue": group_name}],
    )
    if groups_resp.get("Groups"):
        group_id = groups_resp["Groups"][0]["GroupId"]
        logger.info("Identity Center group %s exists", group_name)
    else:
        create_resp = identitystore_client.create_group(
            IdentityStoreId=store_id,
            DisplayName=group_name,
            Description=f"{group_name} (synced from {google_email})",
        )
        group_id = create_resp["GroupId"]
        logger.info("Created Identity Center group %s", group_name)

    # Read members from Google group
    google_members = _get_google_group_members(google_email)

    # Current Identity Center group members
    current_members = {}  # user_id -> membership_id
    next_token = None
    while True:
        kwargs = {"IdentityStoreId": store_id, "GroupId": group_id}
        if next_token:
            kwargs["NextToken"] = next_token
        resp = identitystore_client.list_group_memberships(**kwargs)
        for membership in resp.get("GroupMemberships", []):
            member_id = membership.get("MemberId", {}).get("UserId")
            if member_id:
                current_members[member_id] = membership["MembershipId"]
        next_token = resp.get("NextToken")
        if not next_token:
            break

    # Reverse lookup: email -> user_id for current members
    current_emails = {}
    for user_id in current_members:
        try:
            user = identitystore_client.describe_user(
                IdentityStoreId=store_id, UserId=user_id
            )
            for email_obj in user.get("Emails", []):
                current_emails[email_obj["Value"].lower()] = user_id
        except Exception:
            pass

    # Add Google group members not yet in Identity Center group
    synced_count = 0
    not_provisioned = []
    for email in google_members:
        if email in current_emails:
            synced_count += 1
            continue
        # Look up user in Identity Center (must exist via SCIM)
        users_resp = identitystore_client.list_users(
            IdentityStoreId=store_id,
            Filters=[{"AttributePath": "UserName", "AttributeValue": email}],
        )
        if users_resp.get("Users"):
            user_id = users_resp["Users"][0]["UserId"]
            try:
                identitystore_client.create_group_membership(
                    IdentityStoreId=store_id,
                    GroupId=group_id,
                    MemberId={"UserId": user_id},
                )
                logger.info("Added %s to Identity Center group %s", email, group_name)
                synced_count += 1
            except identitystore_client.exceptions.ConflictException:
                synced_count += 1
        else:
            logger.warning("User %s not in Identity Center (not SCIM-provisioned) — skipping", email)
            not_provisioned.append(email)

    # Remove members no longer in the Google group
    desired = set(google_members)
    for email, user_id in current_emails.items():
        if email not in desired:
            membership_id = current_members.get(user_id)
            if membership_id:
                identitystore_client.delete_group_membership(
                    IdentityStoreId=store_id, MembershipId=membership_id,
                )
                logger.info("Removed %s from Identity Center group %s", email, group_name)

    # Assign permission set (if specified)
    result = {"synced": True, "group": group_name, "member_count": synced_count}
    if not_provisioned:
        result["not_in_identity_center"] = sorted(not_provisioned)
    if permission_set_name and SSO_INSTANCE_ARN:
        result["permission_set"] = _assign_permission_set(group_id, permission_set_name)

    return result


# ---------------------------------------------------------------------------
# AWS access group — who Google provisions to Identity Center
#
# Google's AWS IAM Identity Center app provisions (SCIM) only the DIRECT
# members of the groups in SCIM_GROUPS; nested groups are not expanded. So
# team and kodesmia members are added to AWS_ACCESS_GROUP one by one. Google
# creates their Identity Center user some time later, after this run has
# already skipped them, so the scheduled sync_access_members action retries
# their team-<name> / kodesmia Identity Center memberships.
# ---------------------------------------------------------------------------

AWS_ACCESS_GROUP = os.environ.get("AWS_ACCESS_GROUP", "javabin-developer@java.no").lower()

# Mirrors the AWS app's "Group membership" provisioning setting in Google Admin.
SCIM_GROUPS = tuple(g for g in dict.fromkeys(("drift@java.no", "styret@java.no", AWS_ACCESS_GROUP)) if g)

# Registry groups whose members belong in AWS_ACCESS_GROUP besides team
# members (via `flatten: true` in access.yaml). Used to tell which of the
# group's members the provisioner accounts for.
AWS_ACCESS_REGISTRY_GROUPS = ("kodesmia",)


def _add_direct_members(group_email, wanted, access_token, accounted=()):
    """Add each java.no address in `wanted` as a direct MEMBER of group_email.

    Additive only, like _sync_one_group: nothing is removed. Members present
    in the group but not in `wanted` or `accounted` are reported in
    `preserved`. Non-java.no addresses can't be provisioned to Identity
    Center and are reported in `skipped_external`.
    """
    current = set(_get_google_group_members(group_email))
    group_key = urllib.parse.quote(group_email, safe="")
    added, skipped, failed = [], [], []
    for email in sorted(set(wanted) - current):
        if not email.endswith("@java.no"):
            skipped.append(email)
            logger.warning("Not adding personal address %s to %s", email, group_email)
            continue
        try:
            resp = _google_api(
                "POST", f"/groups/{group_key}/members", access_token,
                {"email": email, "role": "MEMBER"},
            )
        except Exception as e:  # noqa: BLE001 — one bad member must not stop the rest
            failed.append({"email": email, "error": str(e)[:200]})
            continue
        if resp is None:
            failed.append({"email": email, "error": "not in Google Workspace"})
        elif not resp.get("already_exists"):
            added.append(email)
            logger.info("Added %s to %s", email, group_email)

    preserved = sorted(current - set(wanted) - set(accounted))
    if preserved:
        logger.info(
            "Group %s has %d members not from the registry (preserved): %s",
            group_email, len(preserved), ", ".join(preserved[:5]),
        )
    result = {
        "synced": True,
        "group": group_email,
        "member_count": len(current) + len(added),
        "added": len(added),
    }
    if added:
        result["added_members"] = added
    if preserved:
        result["extra_preserved"] = len(preserved)
        result["preserved"] = preserved
    if skipped:
        result["skipped_external"] = skipped
    if failed:
        result["failed"] = failed
    return result


def _recorded_memberships():
    """{group_name: {email}} of registry members in the memberships table.

    sync_cognito_group records every team-<name>, sync_group_to_cognito every
    cognito-enabled registry group (kodesmia, styret, drift, ...).
    """
    if not MEMBERSHIPS_TABLE:
        return {}
    rows, kwargs = {}, {"TableName": MEMBERSHIPS_TABLE}
    while True:
        page = dynamodb_client.scan(**kwargs)
        for item in page.get("Items", []):
            pk = item["pk"]["S"]
            if pk.startswith("group#"):
                rows[pk[len("group#"):]] = {
                    m["S"].lower() for m in item.get("members", {}).get("L", []) if m.get("S")
                }
        if "LastEvaluatedKey" not in page:
            break
        kwargs["ExclusiveStartKey"] = page["LastEvaluatedKey"]
    return rows


def _accounted_access_members(recorded):
    """Recorded members of every team and of AWS_ACCESS_REGISTRY_GROUPS."""
    return {
        email
        for name, emails in recorded.items()
        if name.startswith(TEAM_GROUP_PREFIX) or name in AWS_ACCESS_REGISTRY_GROUPS
        for email in emails
    }


def sync_aws_access_group(teams=None, recorded=None):
    """Make team members direct members of AWS_ACCESS_GROUP so Google SCIM
    creates their Identity Center user.

    `teams` are registry team payloads; None means every team recorded in the
    memberships table. The address is the one _normalize_member derives from
    the member's google field. Additive only (see _add_direct_members).
    """
    if not AWS_ACCESS_GROUP:
        return {"skipped": True, "reason": "aws_access_group_not_configured"}
    if recorded is None:
        try:
            recorded = _recorded_memberships()
        except Exception as e:  # noqa: BLE001 — only the preserved report needs it
            logger.warning("Could not read recorded memberships: %s", e)
            recorded = {}

    wanted = set()
    if teams is None:
        for name, emails in recorded.items():
            if name.startswith(TEAM_GROUP_PREFIX):
                wanted |= emails
    else:
        for team in teams:
            for member in team.get("members", []):
                email = _normalize_member(member).get("email", "").lower()
                if email:
                    wanted.add(email)

    return _add_direct_members(
        AWS_ACCESS_GROUP, wanted, _get_google_access_token(),
        accounted=_accounted_access_members(recorded),
    )


def _sync_flattened_access_group(ag, group_members_map, access_token):
    """`flatten: true` access group: add the role groups' individual members
    as direct members instead of nesting the group emails, because Google's
    SCIM provisioning does not expand nested groups. Additive only."""
    wanted = set()
    for rg in ag.get("groups", []):
        if rg in group_members_map:
            wanted |= {m.lower() for m in group_members_map[rg] if m}
        else:
            logger.warning("Access group %s references unknown group: %s", ag["name"], rg)

    accounted = set()
    if ag["google"].lower() == AWS_ACCESS_GROUP:
        try:
            accounted = _accounted_access_members(_recorded_memberships())
        except Exception as e:  # noqa: BLE001 — only the preserved report needs it
            logger.warning("Could not read recorded memberships: %s", e)
    result = _add_direct_members(ag["google"], wanted, access_token, accounted=accounted)
    result["flatten"] = True
    logger.info(
        "Synced flattened access group %s: %d wanted (+%d)",
        ag["google"], len(wanted), result["added"],
    )
    return result


def _identity_center_reasons(emails, just_added=()):
    """{email: why} for members with no Identity Center user.

    Tells "pending SCIM" (a direct member of a SCIM group, e.g. just added to
    AWS_ACCESS_GROUP) apart from "can't be provisioned" (personal address,
    suspended, not in Google) and "not in a SCIM group".
    """
    emails = sorted(set(emails))
    if not emails:
        return {}
    access_token = _get_google_access_token()
    scim_members = set()
    for group in SCIM_GROUPS:
        try:
            scim_members |= set(_get_google_group_members(group))
        except Exception as e:  # noqa: BLE001
            logger.warning("Could not list %s: %s", group, e)
    just_added = set(just_added)

    reasons = {}
    for email in emails:
        if not email.endswith("@java.no"):
            reasons[email] = "can't be provisioned: personal address, not a java.no account"
            continue
        try:
            user = _google_api("GET", f"/users/{urllib.parse.quote(email, safe='')}", access_token)
        except Exception as e:  # noqa: BLE001
            reasons[email] = f"unknown: Google lookup failed ({str(e)[:80]})"
            continue
        if user is None:
            reasons[email] = "can't be provisioned: not in Google Workspace"
        elif user.get("suspended") or user.get("archived"):
            reasons[email] = "can't be provisioned: suspended in Google"
        elif email in just_added:
            reasons[email] = f"pending SCIM: just added to {AWS_ACCESS_GROUP}"
        elif email in scim_members:
            reasons[email] = "pending SCIM: already a direct member of a provisioned group"
        else:
            reasons[email] = f"not a direct member of {AWS_ACCESS_GROUP}"
    return reasons


def _explain_not_in_identity_center(results, just_added=()):
    """_identity_center_reasons for everyone the IC syncs in `results` skipped."""
    missing = _not_in_identity_center(results)
    if not missing:
        return {}
    try:
        return _identity_center_reasons(missing, just_added)
    except Exception as e:  # noqa: BLE001 — the report must not fail the sync
        logger.error("Could not classify members missing from Identity Center: %s", e)
        return {}


def _identity_center_groups():
    """{display_name: group_id} for every Identity Center group."""
    groups = {}
    next_token = None
    while True:
        kwargs = {"IdentityStoreId": IDENTITY_STORE_ID}
        if next_token:
            kwargs["NextToken"] = next_token
        resp = identitystore_client.list_groups(**kwargs)
        for g in resp.get("Groups", []):
            groups[g.get("DisplayName", "")] = g["GroupId"]
        next_token = resp.get("NextToken")
        if not next_token:
            break
    return groups


def _retry_identity_center_memberships(recorded):
    """Add recorded registry members who now have an Identity Center user to
    the Identity Center group of the same name (team-<name>, kodesmia, ...).

    Additive only: removals stay with the registry syncs. Returns
    {group: {"added": [...], "not_in_identity_center": [...]}} for groups
    with an Identity Center counterpart.
    """
    ic_groups = _identity_center_groups()
    out = {}
    for name, emails in sorted(recorded.items()):
        group_id = ic_groups.get(name)
        if not group_id or not emails:
            continue
        member_ids = set()
        next_token = None
        while True:
            kwargs = {"IdentityStoreId": IDENTITY_STORE_ID, "GroupId": group_id}
            if next_token:
                kwargs["NextToken"] = next_token
            resp = identitystore_client.list_group_memberships(**kwargs)
            for membership in resp.get("GroupMemberships", []):
                member_id = membership.get("MemberId", {}).get("UserId")
                if member_id:
                    member_ids.add(member_id)
            next_token = resp.get("NextToken")
            if not next_token:
                break

        added, missing = [], []
        for email in sorted(emails):
            users = identitystore_client.list_users(
                IdentityStoreId=IDENTITY_STORE_ID,
                Filters=[{"AttributePath": "UserName", "AttributeValue": email}],
            ).get("Users", [])
            if not users:
                missing.append(email)
                continue
            if users[0]["UserId"] in member_ids:
                continue
            try:
                identitystore_client.create_group_membership(
                    IdentityStoreId=IDENTITY_STORE_ID,
                    GroupId=group_id,
                    MemberId={"UserId": users[0]["UserId"]},
                )
                added.append(email)
                logger.info("Added %s to Identity Center group %s (retry)", email, name)
            except identitystore_client.exceptions.ConflictException:
                pass
        out[name] = {"added": added, "not_in_identity_center": missing}
    return out


def handle_sync_access_members(event):
    """Handle the sync_access_members action — EventBridge, every few hours.

    1. Every recorded team member becomes a direct member of AWS_ACCESS_GROUP
       (catches teams synced before this existed, or a failed add).
    2. Recorded members whose Identity Center user Google SCIM has created
       since their registry sync are added to their Identity Center groups.
    Posts to Slack only when someone was added or an add failed.
    """
    try:
        recorded = _recorded_memberships()
    except Exception as e:
        logger.error("Could not read recorded memberships: %s", e)
        return {"statusCode": 500, "body": json.dumps({"error": str(e)[:200]})}

    try:
        access = sync_aws_access_group(None, recorded=recorded)
    except Exception as e:
        logger.error("AWS access group sync failed: %s", e)
        access = {"error": str(e)[:200]}

    retry = {}
    if IDENTITY_STORE_ID:
        try:
            retry = _retry_identity_center_memberships(recorded)
        except Exception as e:
            logger.error("Identity Center retry failed: %s", e)
            retry = {"_error": {"error": str(e)[:200]}}

    # Slack only for news: someone added, or an add failed. A group's still
    # missing members are listed when the group changed or they were just
    # added to AWS_ACCESS_GROUP, so a pending person isn't re-posted every run.
    just_added = set(access.get("added_members", ()))
    changed = {}
    if access.get("added") or access.get("failed") or access.get("error"):
        changed[AWS_ACCESS_GROUP] = {"google": access}
    for name, r in sorted(retry.items()):
        if r.get("error"):
            changed[name] = {"identity_center": r}
        elif r.get("added") or just_added & set(r.get("not_in_identity_center", ())):
            outcome = {
                "synced": True,
                "group": name,
                "added": len(r["added"]),
                "added_members": r["added"],
            }
            if r.get("not_in_identity_center"):
                outcome["not_in_identity_center"] = r["not_in_identity_center"]
            changed[name] = {"identity_center": outcome}

    reasons = {}
    if changed:
        reasons = _explain_not_in_identity_center(changed, just_added)
        try:
            notify_slack(dict(changed), ci_context=event.get("ci_context"), ic_reasons=reasons)
        except Exception as e:
            logger.error("Slack notification failed: %s", e)

    results = {"aws_access_group": access, "identity_center": retry}
    if reasons:
        results["not_in_identity_center"] = reasons
    return {"statusCode": 200, "body": json.dumps(results, default=str)}


DEVELOPER_BOUNDARY_NAME = f"{PROJECT}-developer-boundary"


def _assignment_exists(ps_arn, group_id):
    """True if the group already holds the permission set in the account,
    False if not, None if the lookup failed."""
    kwargs = {"InstanceArn": SSO_INSTANCE_ARN, "AccountId": ACCOUNT_ID, "PermissionSetArn": ps_arn}
    try:
        while True:
            resp = sso_client.list_account_assignments(**kwargs)
            for a in resp.get("AccountAssignments", []):
                if a.get("PrincipalType") == "GROUP" and a.get("PrincipalId") == group_id:
                    return True
            if not resp.get("NextToken"):
                return False
            kwargs["NextToken"] = resp["NextToken"]
    except Exception as e:
        logger.warning("Could not list assignments of %s: %s", ps_arn, e)
        return None


def _create_account_assignment(ps_arn, group_id, label=""):
    """Create an account assignment and wait for it to complete.

    The SSO create_account_assignment API is asynchronous. This function
    polls until the assignment reaches SUCCEEDED or FAILED status.

    Creating an assignment that already exists is accepted (no
    ConflictException), so whether it is new is looked up first:
    "already_existed" is True for an existing one, False for a new one and
    absent when the lookup failed.
    """
    existed = _assignment_exists(ps_arn, group_id)
    if existed:
        logger.info("Permission set %s already assigned to group %s", label, group_id)
        return {"assigned": True, "already_existed": True}
    result = _start_account_assignment(ps_arn, group_id, label)
    if existed is False and result.get("assigned") and "already_existed" not in result:
        result["already_existed"] = False
    return result


def _start_account_assignment(ps_arn, group_id, label):
    try:
        resp = sso_client.create_account_assignment(
            InstanceArn=SSO_INSTANCE_ARN,
            TargetId=ACCOUNT_ID,
            TargetType="AWS_ACCOUNT",
            PermissionSetArn=ps_arn,
            PrincipalType="GROUP",
            PrincipalId=group_id,
        )
    except sso_client.exceptions.ConflictException:
        logger.info("Permission set %s already assigned to group %s", label, group_id)
        return {"assigned": True, "already_existed": True}
    except Exception as e:
        logger.error("Failed to assign %s: %s", label, e)
        return {"error": str(e)[:200]}

    request_id = resp.get("AccountAssignmentCreationStatus", {}).get("RequestId")
    if not request_id:
        logger.info("Assigned %s to group %s (no request ID)", label, group_id)
        return {"assigned": True}

    # Poll for completion
    for _ in range(30):  # max ~30 seconds
        time.sleep(1)
        status_resp = sso_client.describe_account_assignment_creation_status(
            InstanceArn=SSO_INSTANCE_ARN,
            AccountAssignmentCreationRequestId=request_id,
        )
        status = status_resp.get("AccountAssignmentCreationStatus", {})
        state = status.get("Status", "")
        if state == "SUCCEEDED":
            logger.info("Assigned %s to group %s", label, group_id)
            return {"assigned": True}
        if state == "FAILED":
            reason = status.get("FailureReason", "unknown")
            logger.error("Assignment %s failed: %s", label, reason)
            return {"error": reason}

    logger.warning("Assignment %s timed out waiting for completion", label)
    return {"assigned": True, "status": "timed_out"}


def _ensure_team_permission_set(team_name, group_id):
    """Create a per-team permission set and assign it to the team's IC group.

    Guardrails come from javabin-team-{team}-guardrails (platform Terraform):
    the developer boundary's rules with the team name written in. The shared
    developer boundary can't be used here: its rules compare resource tags
    with ${aws:PrincipalTag/team}, and Identity Center sessions carry no team
    tag, so it would deny every team-scoped resource. The inline policy
    allows broad access scoped to resources tagged with the team name.
    """
    ps_name = f"{PROJECT}-team-{team_name}"

    # Check if permission set already exists
    ps_arn = _resolve_permission_set_arn(f"team-{team_name}")
    if not ps_arn:
        try:
            resp = sso_client.create_permission_set(
                InstanceArn=SSO_INSTANCE_ARN,
                Name=ps_name,
                Description=f"Developer access for team {team_name}",
                SessionDuration="PT4H",
            )
            ps_arn = resp["PermissionSet"]["PermissionSetArn"]
            logger.info("Created permission set %s", ps_name)
            _credential_cache[f"_ps_team-{team_name}"] = ps_arn
        except sso_client.exceptions.ConflictException:
            ps_arn = _resolve_permission_set_arn(f"team-{team_name}")
        except Exception as e:
            logger.error("Failed to create permission set %s: %s", ps_name, e)
            return {"error": str(e)[:200]}

    if not ps_arn:
        return {"error": "could not create or find permission set"}

    # Attach the team's guardrails; drop the shared boundary from older setups.
    # Only a policy platform Terraform created: platform CI may not have made
    # it yet for a brand-new team (or never, for non-app teams like
    # platform). Then leave the permission set as it is and report pending.
    guardrails = f"{PROJECT}-team-{team_name}-guardrails"
    state = _guardrails_state(guardrails)
    if state != "ok":
        logger.warning("Not switching %s to %s: %s", ps_name, guardrails, state)
        return {"permission_set": ps_name, "guardrails": state}
    try:
        sso_client.attach_customer_managed_policy_reference_to_permission_set(
            InstanceArn=SSO_INSTANCE_ARN,
            PermissionSetArn=ps_arn,
            CustomerManagedPolicyReference={"Name": guardrails},
        )
    except sso_client.exceptions.ConflictException:
        pass  # already attached
    except Exception as e:
        logger.error("Failed to attach %s to %s: %s", guardrails, ps_name, e)
    try:
        sso_client.detach_customer_managed_policy_reference_from_permission_set(
            InstanceArn=SSO_INSTANCE_ARN,
            PermissionSetArn=ps_arn,
            CustomerManagedPolicyReference={"Name": DEVELOPER_BOUNDARY_NAME},
        )
        logger.info("Detached %s from %s", DEVELOPER_BOUNDARY_NAME, ps_name)
    except sso_client.exceptions.ResourceNotFoundException:
        pass  # not attached
    except Exception as e:
        logger.error("Failed to detach %s from %s: %s", DEVELOPER_BOUNDARY_NAME, ps_name, e)

    # Set inline policy — broad allow scoped to team-tagged resources
    policy = {
        "Version": "2012-10-17",
        "Statement": [
            {
                "Sid": "AllowTeamResources",
                "Effect": "Allow",
                "Action": "*",
                "Resource": "*",
                "Condition": {
                    "StringEqualsIfExists": {
                        "aws:ResourceTag/team": team_name,
                    }
                },
            },
            {
                # Platform-managed parameters in the team path: Cognito
                # credentials and auth-status (app-auth-sync), the auth
                # request (app Terraform), RDS master passwords (the DB
                # keeps the original). Teams can still read them.
                "Sid": "DenyPlatformManagedParameters",
                "Effect": "Deny",
                "Action": ["ssm:PutParameter", "ssm:DeleteParameter", "ssm:DeleteParameters",
                           "ssm:LabelParameterVersion"],
                "Resource": [
                    f"arn:aws:ssm:*:{ACCOUNT_ID}:parameter/{PROJECT}/apps/*/*/cognito-client-*",
                    f"arn:aws:ssm:*:{ACCOUNT_ID}:parameter/{PROJECT}/apps/*/*/auth",
                    f"arn:aws:ssm:*:{ACCOUNT_ID}:parameter/{PROJECT}/apps/*/*/auth-status",
                    f"arn:aws:ssm:*:{ACCOUNT_ID}:parameter/{PROJECT}/apps/*/*/db-master-password",
                ],
            },
            {
                # Only overwrite secrets the platform created: a hand-made
                # parameter makes the app's next terraform apply fail.
                # (PutParameter supports ssm:Overwrite, not aws:ResourceTag.)
                "Sid": "DenyCreatingParameters",
                "Effect": "Deny",
                "Action": "ssm:PutParameter",
                "Resource": f"arn:aws:ssm:*:{ACCOUNT_ID}:parameter/{PROJECT}/apps/*",
                "Condition": {"BoolIfExists": {"ssm:Overwrite": "false"}},
            },
            {
                # ECS Exec into the team's tasks (apps with compute.exec).
                # ExecuteCommand is authorized against the cluster and the
                # task: the shared cluster is allowed here, the task only by
                # AllowTeamResources (its propagated team tag), so another
                # team's tasks stay out of reach.
                "Sid": "AllowExecOnPlatformCluster",
                "Effect": "Allow",
                "Action": "ecs:ExecuteCommand",
                "Resource": f"arn:aws:ecs:*:{ACCOUNT_ID}:cluster/{PROJECT}-platform",
            },
            {
                "Sid": "AllowUntaggedReads",
                "Effect": "Allow",
                "Action": [
                    "ecs:ListClusters",
                    "ecs:ListServices",
                    "ecr:GetAuthorizationToken",
                    "elasticloadbalancing:Describe*",
                    "route53:ListHostedZones",
                    "logs:DescribeLogGroups",
                    "cloudwatch:ListMetrics",
                    "ce:GetCostAndUsage",
                    "ce:GetCostForecast",
                ],
                "Resource": "*",
            },
        ],
    }

    try:
        sso_client.put_inline_policy_to_permission_set(
            InstanceArn=SSO_INSTANCE_ARN,
            PermissionSetArn=ps_arn,
            InlinePolicy=json.dumps(policy),
        )
    except Exception as e:
        logger.error("Failed to set inline policy for %s: %s", ps_name, e)
        return {"error": str(e)[:200]}

    # Assign to group and wait for completion
    result = _create_account_assignment(ps_arn, group_id, ps_name)
    result["permission_set"] = ps_name
    # Push policy changes to the role in the account (needed for existing
    # assignments; a new assignment provisions on creation)
    result["provisioned"] = _provision_permission_set(ps_arn, ps_name)
    return result


def _guardrails_state(policy_name):
    """'ok' if the team guardrails policy exists and was created by platform
    Terraform; otherwise a short reason (it is attached by name, so a
    look-alike created by someone else must never be used)."""
    arn = f"arn:aws:iam::{ACCOUNT_ID}:policy/{policy_name}"
    try:
        tags = {t["Key"]: t["Value"] for t in iam_client.list_policy_tags(PolicyArn=arn)["Tags"]}
    except iam_client.exceptions.NoSuchEntityException:
        return ("pending: policy not created yet (platform CI creates it for registry "
                "teams, then grants access after its apply)")
    if tags.get("managed-by") != "terraform" or tags.get("repo") != f"{GITHUB_ORG}/platform":
        return "refused: policy exists but was not created by platform Terraform"
    return "ok"


def _provision_permission_set(ps_arn, label):
    """Re-provision a permission set to the account and wait (up to ~60 s)."""
    try:
        resp = sso_client.provision_permission_set(
            InstanceArn=SSO_INSTANCE_ARN,
            PermissionSetArn=ps_arn,
            TargetId=ACCOUNT_ID,
            TargetType="AWS_ACCOUNT",
        )
    except Exception as e:
        logger.error("Failed to provision %s: %s", label, e)
        return {"error": str(e)[:200]}
    request_id = resp.get("PermissionSetProvisioningStatus", {}).get("RequestId")
    if not request_id:
        return {"status": "requested"}
    for _ in range(60):
        time.sleep(1)
        try:
            status = sso_client.describe_permission_set_provisioning_status(
                InstanceArn=SSO_INSTANCE_ARN,
                ProvisionPermissionSetRequestId=request_id,
            ).get("PermissionSetProvisioningStatus", {})
        except Exception as e:
            logger.error("Provisioning status for %s unavailable: %s", label, e)
            return {"status": "unknown", "error": str(e)[:200]}
        if status.get("Status") == "SUCCEEDED":
            return {"status": "SUCCEEDED"}
        if status.get("Status") == "FAILED":
            # e.g. the guardrails policy doesn't exist yet for a brand-new team:
            # platform CI creates it, and the next provisioning run succeeds
            logger.error("Provisioning %s failed: %s", label, status.get("FailureReason"))
            return {"status": "FAILED", "reason": status.get("FailureReason", "")[:200]}
    return {"status": "timed_out"}


def _resolve_permission_set_arn(name):
    """Look up a permission set ARN by name (e.g. 'admin' -> javabin-admin ARN).

    Results are cached across invocations.
    """
    cache_key = f"_ps_{name}"
    if cache_key in _credential_cache:
        return _credential_cache[cache_key]

    full_name = f"{PROJECT}-{name}"
    paginator = sso_client.get_paginator("list_permission_sets")
    for page in paginator.paginate(InstanceArn=SSO_INSTANCE_ARN):
        for ps_arn in page.get("PermissionSets", []):
            resp = sso_client.describe_permission_set(
                InstanceArn=SSO_INSTANCE_ARN, PermissionSetArn=ps_arn,
            )
            if resp["PermissionSet"]["Name"] == full_name:
                _credential_cache[cache_key] = ps_arn
                return ps_arn

    logger.warning("Permission set %s not found", full_name)
    return None


def _assign_permission_set(group_id, permission_set_name):
    """Assign a permission set to an Identity Center group for the AWS account."""
    ps_arn = _resolve_permission_set_arn(permission_set_name)
    if not ps_arn:
        return {"skipped": True, "reason": f"permission set '{permission_set_name}' not found"}

    return _create_account_assignment(ps_arn, group_id, permission_set_name)


# ---------------------------------------------------------------------------
# Team AWS access — finish permission sets once platform CI made guardrails
# ---------------------------------------------------------------------------

TEAM_GROUP_PREFIX = "team-"


def _team_groups():
    """{team_name: group_id} for every team-<name> Identity Center group."""
    return {
        name[len(TEAM_GROUP_PREFIX):]: group_id
        for name, group_id in _identity_center_groups().items()
        if name.startswith(TEAM_GROUP_PREFIX) and _validate_team_name(name[len(TEAM_GROUP_PREFIX):])
    }


def _sync_one_team_access(team_name, group_id):
    """Attach guardrails, set the inline policy and assign the team's permission
    set. Teams without a platform-made guardrails policy are left untouched."""
    state = _guardrails_state(f"{PROJECT}-team-{team_name}-guardrails")
    if state != "ok":
        return {"permission_set": f"{PROJECT}-team-{team_name}", "guardrails": state}
    return _ensure_team_permission_set(team_name, group_id)


def handle_sync_team_access(event):
    """Handle the sync_team_access action — queued by platform CI after apply.

    A new team's guardrails policy only exists once platform CI applies on
    teams-changed, which runs after the registry's team sync, so that sync
    stops at "guardrails pending". This finishes the job for every team-*
    Identity Center group (or the teams in event["teams"]): idempotent, it
    also rolls inline policy changes out to existing teams. Posts to Slack
    only when access was newly granted or something failed.
    """
    if not (IDENTITY_STORE_ID and SSO_INSTANCE_ARN and ACCOUNT_ID):
        return {"statusCode": 200, "body": "Identity Center not configured"}

    groups = _team_groups()
    wanted = event.get("teams")
    if wanted:
        groups = {t: g for t, g in groups.items() if t in set(wanted)}

    results = {}
    with ThreadPoolExecutor(max_workers=GROUP_SYNC_WORKERS) as executor:
        futures = {
            executor.submit(_sync_one_team_access, team, group_id): team
            for team, group_id in groups.items()
        }
        for future in as_completed(futures):
            team = futures[future]
            try:
                results[team] = future.result()
            except Exception as e:
                logger.error("Team access sync failed for %s: %s", team, e)
                results[team] = {"error": str(e)[:200]}

    def _noteworthy(r):
        provisioned = r.get("provisioned") or {}
        return bool(
            r.get("error")
            or (r.get("assigned") and r.get("already_existed") is False)
            or (isinstance(provisioned, dict) and provisioned.get("status") == "FAILED")
        )

    changed = {
        team: {"aws_access": _access_line(r)}
        for team, r in sorted(results.items()) if _noteworthy(r)
    }
    if changed:
        try:
            notify_slack(changed, ci_context=event.get("ci_context"))
        except Exception as e:
            logger.error("Slack notification failed: %s", e)

    return {
        "statusCode": 200,
        "body": json.dumps({"synced": sorted(results), "results": results}, default=str),
    }


def _access_line(result):
    """Shape a permission-set result for _result_line."""
    provisioned = result.get("provisioned") or {}
    if result.get("error"):
        return {"error": result["error"]}
    if isinstance(provisioned, dict) and provisioned.get("status") == "FAILED":
        return {"error": f"provisioning failed: {provisioned.get('reason', '')}"}
    return {"synced": True, "group": result.get("permission_set", "")}


def _sync_hero_account(hero, access_token):
    """Check/create a single hero's Google Workspace account.

    Returns dict with keys: email, status ("existed"|"created"|"failed"|"skipped"),
    and optional "error" for failed.
    """
    email = hero.get("javabin_google_email", "")
    if not email:
        return {"status": "skipped"}

    user_key = urllib.parse.quote(email, safe="")
    existing_user = _google_api("GET", f"/users/{user_key}", access_token)
    if existing_user and not existing_user.get("already_exists"):
        logger.info("Google Workspace account already exists: %s", email)
        return {"email": email, "status": "existed"}

    import secrets
    import string
    temp_password = "".join(
        secrets.choice(string.ascii_letters + string.digits + "!@#$%") for _ in range(24)
    )
    personal_email = hero.get("personal_email", "")
    user_body = {
        "primaryEmail": email,
        "name": {
            "givenName": hero.get("firstname", ""),
            "familyName": hero.get("lastname", ""),
        },
        "password": temp_password,
        "changePasswordAtNextLogin": True,
        "recoveryEmail": personal_email,
    }

    try:
        create_result = _google_api("POST", "/users", access_token, user_body)
        if create_result and not create_result.get("already_exists"):
            logger.info("Created Google Workspace account: %s (recovery: %s)", email, personal_email)
            if personal_email:
                try:
                    _setup_email_forwarding(email, personal_email)
                except Exception as fe:
                    logger.warning("Could not set up forwarding %s → %s: %s", email, personal_email, fe)
                try:
                    pw_url = _generate_password_set_url(email)
                    _send_welcome_email(
                        access_token, email, personal_email,
                        hero.get("firstname", ""), password_set_url=pw_url,
                    )
                except Exception as we:
                    logger.warning("Could not send welcome email to %s: %s", personal_email, we)
            return {"email": email, "status": "created"}
        # 409 / already_exists — still ensure forwarding
        if personal_email:
            try:
                _setup_email_forwarding(email, personal_email)
            except Exception as fe:
                logger.warning("Could not set up forwarding %s → %s: %s", email, personal_email, fe)
        return {"email": email, "status": "existed"}
    except Exception as e:
        logger.error("Failed to create account %s: %s", email, e)
        return {"email": email, "status": "failed", "error": str(e)[:200]}


def _sync_hero_alias(hero, access_token):
    """Check/create one hero's email alias. Returns dict or None if no alias."""
    alias = hero.get("alias", "")
    email = hero.get("javabin_google_email", "")
    if not alias or not email:
        return None

    user_key = urllib.parse.quote(email, safe="")
    try:
        existing_aliases = _google_api("GET", f"/users/{user_key}/aliases", access_token)
        existing_alias_emails = set()
        if existing_aliases and "aliases" in existing_aliases:
            existing_alias_emails = {a["alias"].lower() for a in existing_aliases["aliases"]}

        if alias.lower() not in existing_alias_emails:
            alias_result = _google_api(
                "POST", f"/users/{user_key}/aliases", access_token, {"alias": alias}
            )
            if alias_result:
                logger.info("Created alias %s for %s", alias, email)
                return {"status": "created", "user": email, "alias": alias}
        return {"status": "exists", "user": email, "alias": alias}
    except Exception as e:
        logger.error("Failed to create alias %s for %s: %s", alias, email, e)
        return {"status": "failed", "user": email, "alias": alias, "error": str(e)[:200]}


def _sync_one_group(group, access_token):
    """Sync a single group: Google Workspace + optional Cognito + Identity Center.

    Returns (name, result_dict) where result_dict has keys "google",
    optionally "cognito", "identity_center". Returns (None, None) if invalid.
    """
    name = group.get("name")
    google_email = group.get("google")
    members = group.get("members", [])

    if not name or not google_email:
        return None, None

    logger.info("Syncing group: %s (%s) with %d members", name, google_email, len(members))
    gr = {}

    try:
        group_key = urllib.parse.quote(google_email, safe="")
        group_body = {
            "email": google_email,
            "name": name,
            "description": f"javaBin group: {name}",
        }

        existing = _google_api("GET", f"/groups/{group_key}", access_token)
        if existing and not existing.get("already_exists"):
            # Group exists — do NOT update name/description/email.
            # Updating existing groups can rename unrelated groups that
            # share a display name or alias (e.g. jz-ledelsen → javazone).
            logger.info("Google group %s already exists — skipping metadata update", google_email)
        else:
            _google_api("POST", "/groups", access_token, group_body)

        current_emails = set()
        page_token = None
        while True:
            path = f"/groups/{group_key}/members?maxResults=200"
            if page_token:
                path += f"&pageToken={urllib.parse.quote(page_token, safe='')}"
            resp = _google_api("GET", path, access_token)
            if resp and "members" in resp:
                for m in resp["members"]:
                    current_emails.add(m["email"].lower())
            if resp and resp.get("nextPageToken"):
                page_token = resp["nextPageToken"]
            else:
                break

        desired_emails = {m.lower() for m in members if m}

        added = 0
        for email in desired_emails - current_emails:
            _google_api(
                "POST", f"/groups/{group_key}/members", access_token,
                {"email": email, "role": "MEMBER"},
            )
            added += 1

        # Additive only — never remove members automatically.
        # Members added manually via Google Admin UI are preserved.
        extra = current_emails - desired_emails
        if extra:
            logger.info(
                "Group %s has %d members not in YAML (preserved): %s",
                google_email, len(extra), ", ".join(sorted(extra)[:5]),
            )

        gr["google"] = {
            "synced": True,
            "group": google_email,
            "member_count": len(desired_emails | current_emails),
            "added": added,
            "extra_preserved": len(extra),
        }
    except Exception as e:
        logger.error("Google group sync failed for %s: %s", name, e)
        gr["google"] = {"error": str(e)[:200]}

    if group.get("cognito"):
        try:
            gr["cognito"] = sync_group_to_cognito(group)
        except Exception as e:
            logger.error("Cognito sync failed for %s: %s", name, e)
            gr["cognito"] = {"error": str(e)[:200]}

    if group.get("identity_center"):
        try:
            gr["identity_center"] = sync_group_to_identity_center(group)
        except Exception as e:
            logger.error("Identity Center sync failed for %s: %s", name, e)
            gr["identity_center"] = {"error": str(e)[:200]}

    return name, gr


def _sync_one_access_group(ag, group_email_map, access_token, group_members_map=None):
    """Sync a single access group. Returns (name, result_dict).

    Role groups are nested as members, or with `flatten: true` their
    individual members are added directly (see _sync_flattened_access_group).
    """
    ag_name = ag.get("name", "")
    ag_google = ag.get("google", "")
    role_groups = ag.get("groups", [])
    if not ag_name or not ag_google:
        return None, None

    ag_key = urllib.parse.quote(ag_google, safe="")
    try:
        existing_ag = _google_api("GET", f"/groups/{ag_key}", access_token)
        if not existing_ag or existing_ag.get("already_exists"):
            _google_api("POST", "/groups", access_token, {
                "email": ag_google,
                "name": ag_name,
                "description": ag.get("description", f"Access group: {ag_name}"),
            })
            logger.info("Created access group %s", ag_google)

        if ag.get("flatten"):
            return ag_name, _sync_flattened_access_group(ag, group_members_map or {}, access_token)

        current_members = set()
        page_token = None
        while True:
            path = f"/groups/{ag_key}/members?maxResults=200"
            if page_token:
                path += f"&pageToken={urllib.parse.quote(page_token, safe='')}"
            resp = _google_api("GET", path, access_token)
            if resp and "members" in resp:
                for m in resp["members"]:
                    current_members.add(m["email"].lower())
            if resp and resp.get("nextPageToken"):
                page_token = resp["nextPageToken"]
            else:
                break

        desired_members = set()
        for rg in role_groups:
            rg_email = group_email_map.get(rg)
            if rg_email:
                desired_members.add(rg_email.lower())
            else:
                logger.warning("Access group %s references unknown group: %s", ag_name, rg)

        added = 0
        for email in desired_members - current_members:
            _google_api(
                "POST", f"/groups/{ag_key}/members", access_token,
                {"email": email, "role": "MEMBER"},
            )
            added += 1

        removed = 0
        managed_group_emails = {v.lower() for v in group_email_map.values()}
        for email in current_members - desired_members:
            # Only remove group emails (containing @java.no) that we manage.
            # Preserve any directly-added individual members.
            if email.endswith("@java.no") and email in managed_group_emails:
                member_key = urllib.parse.quote(email, safe="")
                _google_api("DELETE", f"/groups/{ag_key}/members/{member_key}", access_token)
                removed += 1

        logger.info(
            "Synced access group %s: %d role groups (+%d/-%d)",
            ag_google, len(desired_members), added, removed,
        )
        return ag_name, {
            "synced": True,
            "added": added,
            "removed": removed,
            "total_groups": len(desired_members),
        }
    except Exception as e:
        logger.error("Access group sync failed for %s: %s", ag_name, e)
        return ag_name, {"error": str(e)[:200]}


def handle_sync_groups_and_heros(event):
    """Handle the sync_groups_and_heros action — full hero + group reconciliation.

    Processes both groups and heroes in a single pass:
    1. Auto-create Google Workspace accounts for new heroes (parallel)
    2. Manage email aliases for heroes (parallel)
    3. Sync Google Workspace groups with resolved member lists (parallel)
    4. Sync Cognito groups where flagged (per-group, inside Step 3)
    5. Sync Identity Center groups where flagged (per-group, inside Step 3)
    6. Sync access groups (nested role-group membership, or the role groups'
       individual members for `flatten: true`, parallel)

    Per-item work in each step is dispatched via ThreadPoolExecutor with
    GROUP_SYNC_WORKERS workers, sized to stay within Google Directory API's
    per-user quota (1,500 queries / 100 s).
    """
    groups = event.get("groups", [])
    heros = event.get("heros", [])

    results = {}

    # Pre-fetch the OAuth token once so parallel workers don't all race to refresh it.
    access_token = _get_google_access_token()

    # Step 1: Auto-create Google Workspace accounts for heroes (parallel)
    accounts_created = []
    accounts_failed = []
    accounts_existed = []

    with ThreadPoolExecutor(max_workers=GROUP_SYNC_WORKERS) as executor:
        for r in executor.map(lambda h: _sync_hero_account(h, access_token), heros):
            status = r.get("status")
            if status == "created":
                accounts_created.append(r["email"])
            elif status == "existed":
                accounts_existed.append(r["email"])
            elif status == "failed":
                accounts_failed.append({"email": r["email"], "error": r.get("error", "")})

    results["accounts"] = {
        "created": accounts_created,
        "existed": accounts_existed,
        "failed": accounts_failed,
    }

    # Step 2: Manage email aliases (parallel)
    aliases_created = []
    aliases_failed = []

    with ThreadPoolExecutor(max_workers=GROUP_SYNC_WORKERS) as executor:
        for r in executor.map(lambda h: _sync_hero_alias(h, access_token), heros):
            if not r:
                continue
            if r["status"] == "created":
                aliases_created.append({"user": r["user"], "alias": r["alias"]})
            elif r["status"] == "failed":
                aliases_failed.append({
                    "user": r["user"], "alias": r["alias"], "error": r.get("error", ""),
                })

    results["aliases"] = {
        "created": aliases_created,
        "failed": aliases_failed,
    }

    # Step 3 (+ 4, 5): Sync Google groups, Cognito, Identity Center per group (parallel)
    with ThreadPoolExecutor(max_workers=GROUP_SYNC_WORKERS) as executor:
        futures = [executor.submit(_sync_one_group, group, access_token) for group in groups]
        for future in as_completed(futures):
            name, gr = future.result()
            if name:
                results[name] = gr

    # Step 6: Sync access groups (depends on Step 3 — Google groups must exist first)
    access_groups = event.get("access_groups", [])
    access_results = {}
    if access_groups:
        group_email_map = {
            g.get("name"): g.get("google")
            for g in groups if g.get("name") and g.get("google")
        }
        group_members_map = {
            g.get("name"): g.get("members", [])
            for g in groups if g.get("name")
        }
        with ThreadPoolExecutor(max_workers=GROUP_SYNC_WORKERS) as executor:
            futures = [
                executor.submit(
                    _sync_one_access_group, ag, group_email_map, access_token, group_members_map,
                )
                for ag in access_groups
            ]
            for future in as_completed(futures):
                name, gr = future.result()
                if name:
                    access_results[name] = gr

    if access_results:
        results["_access_groups"] = access_results

    just_added = {
        email for r in access_results.values() if isinstance(r, dict)
        for email in r.get("added_members", [])
    }
    ic_reasons = _explain_not_in_identity_center(results, just_added)

    try:
        notify_slack(results, ci_context=event.get("ci_context"), ic_reasons=ic_reasons)
    except Exception as e:
        logger.error("Slack notification failed: %s", e)
    if ic_reasons:
        results["not_in_identity_center"] = ic_reasons


    # Full group list: drop memberships of removed / no-longer-cognito groups
    try:
        results["auth_memberships_pruned"] = _prune_memberships(
            {g["name"] for g in groups if g.get("cognito")})
    except Exception as e:
        logger.error("Pruning auth memberships failed: %s", e)

    return {
        "statusCode": 200,
        "body": json.dumps(
            {"synced": list(results.keys()), "results": results},
            default=str,
        ),
    }


def handle_sync_atlassian(event):
    """Handle the sync_atlassian action — registry groups → Atlassian groups.

    Event: {"action": "sync_atlassian", "groups": [{"name", "description", "members": [email]}]}
    Writes only when ATLASSIAN_SYNC_APPLY is enabled; otherwise returns the plan.
    """
    admin = atlassian.AtlassianAdmin(
        _get_ssm_param(ATLASSIAN_ORG_ID_PARAM), _get_ssm_param(ATLASSIAN_API_KEY_PARAM),
    )
    results = atlassian.sync(admin, event.get("groups", []), apply=ATLASSIAN_SYNC_APPLY)

    try:
        text = atlassian.summary_text(results, ATLASSIAN_SYNC_APPLY)
        source = _format_ci_source(event.get("ci_context"))
        if source:
            text += f"\n{source}"
        post_to_slack(get_webhook_url(ssm, INFRA_WEBHOOK_PARAM), {"text": text})
    except Exception as e:
        logger.error("Slack notification failed: %s", e)

    return {
        "statusCode": 200,
        "body": json.dumps({"apply": ATLASSIAN_SYNC_APPLY, "results": results}),
    }


def _generate_password_set_url(email):
    """Generate a signed password-set URL for a new hero account.

    Uses the same HMAC-SHA256 token format as the password-set Lambda.
    Reads the function URL from SSM (avoids circular TF dependency).
    """
    import hashlib
    import hmac
    import uuid

    try:
        base_url = _get_ssm_param(PASSWORD_SET_URL_PARAM).rstrip("/")
    except Exception as e:
        logger.warning("Could not read interactive function URL from SSM: %s", e)
        return None

    signing_key = _get_ssm_param(SIGNING_KEY_PARAM)
    payload = json.dumps({
        "email": email,
        "exp": int(time.time()) + 60 * 86400,  # 60 days — single-use
        "jti": str(uuid.uuid4()),
    })
    payload_b64 = _b64url(payload)
    sig = hmac.new(
        signing_key.encode("utf-8"), payload_b64.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    token = f"{payload_b64}.{sig}"

    return f"{base_url}/set-password?token={token}"


def handle_resend_password_link(event):
    """Handle the resend_password_link action — generate a new password-set link and email it.

    Use when the original 60-day link has expired. Can be triggered by direct Lambda
    invocation with: {"action": "resend_password_link", "email": "user@java.no",
    "personal_email": "user@gmail.com", "firstname": "Name"}
    """
    email = event.get("email", "")
    personal_email = event.get("personal_email", "")
    firstname = event.get("firstname", email.split("@")[0])

    if not email or not personal_email:
        return {"statusCode": 400, "body": "Missing email or personal_email"}

    access_token = _get_google_access_token()
    pw_url = _generate_password_set_url(email)
    if not pw_url:
        return {"statusCode": 500, "body": "Could not generate password-set URL"}

    _send_welcome_email(access_token, email, personal_email, firstname, password_set_url=pw_url)
    logger.info("Resent password-set link for %s to %s", email, personal_email)
    return {"statusCode": 200, "body": f"Password-set link sent to {personal_email}"}


def handle_set_password(event):
    """Handle the set_password action — set a user's Google Workspace password.

    Called by the password-set Lambda after validating the password-set token.
    """
    email = event.get("email", "")
    password = event.get("password", "")

    if not email or not password:
        return {"statusCode": 400, "body": "Missing email or password"}

    access_token = _get_google_access_token()
    user_key = urllib.parse.quote(email, safe="")

    result = _google_api("PATCH", f"/users/{user_key}", access_token, {
        "password": password,
        "changePasswordAtNextLogin": False,
    })

    if result is None:
        return {"statusCode": 404, "body": f"User not found: {email}"}

    logger.info("Password set for %s via self-service flow", email)
    return {"statusCode": 200, "body": f"Password set for {email}"}


def handle_sync_groups(event):
    """Handle the sync_groups action — process groups.yaml entries."""
    groups = event.get("groups", [])
    if not groups:
        return {"statusCode": 200, "body": "No groups to sync"}

    results = {}
    for group in groups:
        name = group.get("name")
        if not name or not group.get("google"):
            continue

        logger.info("Syncing group: %s (%s)", name, group["google"])
        gr = {}

        try:
            gr["cognito"] = sync_group_to_cognito(group)
        except Exception as e:
            logger.error("Cognito sync failed for %s: %s", name, e)
            gr["cognito"] = {"error": str(e)[:200]}

        try:
            gr["identity_center"] = sync_group_to_identity_center(group)
        except Exception as e:
            logger.error("Identity Center sync failed for %s: %s", name, e)
            gr["identity_center"] = {"error": str(e)[:200]}

        results[name] = gr

    try:
        notify_slack(results, ci_context=event.get("ci_context"))
    except Exception as e:
        logger.error("Slack notification failed: %s", e)

    return {
        "statusCode": 200,
        "body": json.dumps({"synced": list(results.keys()), "results": results}, default=str),
    }


# ---------------------------------------------------------------------------
# Event parsing
# ---------------------------------------------------------------------------

def _extract_payload(event):
    """Unwrap the event payload from SNS, Function URL, or direct invocation."""
    if "Records" in event:
        for record in event["Records"]:
            if "Sns" in record:
                return json.loads(record["Sns"]["Message"])

    if "body" in event:
        body = event["body"]
        return json.loads(body) if isinstance(body, str) else body

    return event


def parse_team_payload(event):
    """Extract team definitions from the Lambda event.

    Supports three invocation patterns:
    1. Direct: {"teams": [...]}
    2. SNS-wrapped: {"Records": [{"Sns": {"Message": '{"teams": [...]}'}}]}
    3. Function URL: {"body": '{"teams": [...]}'}
    """
    payload = _extract_payload(event)
    if "teams" in payload:
        return payload["teams"]

    logger.warning(
        "No team data found in event: %s",
        json.dumps(event, default=str)[:500],
    )
    return []


# ---------------------------------------------------------------------------
# Slack notification
# ---------------------------------------------------------------------------

def _result_line(provider, outcome):
    """Format a single provider sync result for Slack."""
    if not isinstance(outcome, dict):
        return f":question: {provider}: {outcome}"
    if outcome.get("skipped"):
        reason = outcome.get("reason", "skipped")
        return f":white_circle: {provider}: {reason}"
    if outcome.get("error"):
        return f":red_circle: {provider}: {outcome['error'][:200]}"
    # Include detail counts when available (google group sync)
    parts = [f":white_check_mark: {provider}: synced"]
    details = []
    if outcome.get("added"):
        details.append(f"+{outcome['added']} added")
    if outcome.get("member_count"):
        details.append(f"{outcome['member_count']} total")
    if outcome.get("extra_preserved"):
        details.append(f"{outcome['extra_preserved']} manual")
    if outcome.get("role_changes"):
        details.append(f"{len(outcome['role_changes'])} role change(s)")
    settings = outcome.get("settings")
    if isinstance(settings, dict) and settings.get("changed"):
        details.append(f"settings: {', '.join(settings['changed'])}")
    if details:
        parts.append(f"({', '.join(details)})")
    if isinstance(settings, dict):
        if settings.get("skipped"):
            parts.append(f":warning: group settings skipped: {settings.get('reason')}")
        elif settings.get("error"):
            parts.append(f":warning: group settings: {settings['error'][:150]}")
    if outcome.get("added_members"):
        parts.append(f"added: {', '.join(outcome['added_members'][:20])}")
    if outcome.get("preserved"):
        parts.append(
            f":information_source: kept, not in the registry groups or a team: "
            f"{', '.join(outcome['preserved'][:20])}"
        )
    if outcome.get("failed"):
        failed = ", ".join(f"{f['email']} ({f['error'][:60]})" for f in outcome["failed"][:10])
        parts.append(f":warning: could not add: {failed}")
    if outcome.get("skipped_external"):
        parts.append(f":warning: not added (not @java.no): {', '.join(outcome['skipped_external'])}")
    if outcome.get("not_in_identity_center"):
        parts.append(
            f":warning: no AWS access, not in Identity Center: {', '.join(outcome['not_in_identity_center'])}"
        )
    ps = outcome.get("permission_set")
    if isinstance(ps, dict):
        if ps.get("guardrails"):
            parts.append(f":hourglass_flowing_sand: AWS access {ps['guardrails']}")
        elif ps.get("error"):
            parts.append(f":warning: AWS access: {ps['error'][:150]}")
    return " ".join(parts)


NOT_IN_IDENTITY_CENTER_HELP = (
    f"Google provisions only direct members of {', '.join(SCIM_GROUPS)} to Identity Center. "
    f"The provisioner adds team and kodesmia members to {AWS_ACCESS_GROUP}; once Google has "
    "created their user, the scheduled sync (every 3 hours) adds them to their groups. "
    "Members marked can't be provisioned need a java.no account that is active in Google."
)


def _not_in_identity_center(results):
    """{email: [group, ...]} for members the IC sync couldn't add."""
    missing = {}
    for name, result in results.items():
        if not isinstance(result, dict):
            continue
        outcome = result.get("identity_center")
        if not isinstance(outcome, dict):
            continue
        group = outcome.get("group", name)
        for email in outcome.get("not_in_identity_center", []):
            missing.setdefault(email, []).append(group)
    return missing


def _not_in_identity_center_block(results, reasons=None):
    """Slack section listing everyone without AWS access, or None.

    `reasons` ({email: why}, see _identity_center_reasons) is appended per line.
    """
    missing = _not_in_identity_center(results)
    if not missing:
        return None
    reasons = reasons or {}
    lines = [
        f"• {email} ({', '.join(sorted(groups))})" + (f": {reasons[email]}" if email in reasons else "")
        for email, groups in sorted(missing.items())
    ]
    text = (
        f":warning: *{len(missing)} member(s) have no AWS access: not in Identity Center*\n"
        + "\n".join(lines[:40])
        + ("\n…" if len(lines) > 40 else "")
        + f"\n{NOT_IN_IDENTITY_CENTER_HELP}"
    )
    return {"type": "section", "text": {"type": "mrkdwn", "text": text[:2900]}}


def _status_cell(status):
    """Build a rich_text table cell with emoji for account/alias status."""
    emoji_map = {
        "created": ("white_check_mark", "Created"),
        "existed": ("heavy_minus_sign", "Existed"),
        "failed": ("x", "Failed"),
    }
    emoji_name, label = emoji_map.get(status, ("question", status))
    return {
        "type": "rich_text",
        "elements": [{
            "type": "rich_text_section",
            "elements": [
                {"type": "emoji", "name": emoji_name},
                {"type": "text", "text": f" {label}"},
            ],
        }],
    }


def _hero_sync_table(accounts, aliases):
    """Build a single table for all hero account + alias provisioning.

    Combines accounts and aliases into one table. Returns None if nothing happened.
    Only 1 table block allowed per Slack message.
    """
    created = accounts.get("created", [])
    existed = accounts.get("existed", [])
    failed = accounts.get("failed", [])
    aliases_created = aliases.get("created", []) if aliases else []
    aliases_failed = aliases.get("failed", []) if aliases else []

    if not any([created, existed, failed, aliases_created, aliases_failed]):
        return None

    header = [
        {"type": "raw_text", "text": "Type"},
        {"type": "raw_text", "text": "Email / Alias"},
        {"type": "raw_text", "text": "Status"},
    ]
    rows = [header]

    for email in created:
        rows.append([
            {"type": "raw_text", "text": "Account"},
            {"type": "raw_text", "text": email},
            _status_cell("created"),
        ])
    for email in existed:
        rows.append([
            {"type": "raw_text", "text": "Account"},
            {"type": "raw_text", "text": email},
            _status_cell("existed"),
        ])
    for entry in failed:
        email = entry.get("email", "?")
        error = entry.get("error", "unknown")[:80]
        rows.append([
            {"type": "raw_text", "text": "Account"},
            {"type": "raw_text", "text": f"{email} ({error})"},
            _status_cell("failed"),
        ])
    for entry in aliases_created:
        rows.append([
            {"type": "raw_text", "text": "Alias"},
            {"type": "raw_text", "text": f"{entry.get('alias', '?')} -> {entry.get('user', '?')}"},
            _status_cell("created"),
        ])
    for entry in aliases_failed:
        rows.append([
            {"type": "raw_text", "text": "Alias"},
            {"type": "raw_text", "text": f"{entry.get('alias', '?')} ({entry.get('error', '')[:60]})"},
            _status_cell("failed"),
        ])

    return {
        "type": "table",
        "column_settings": [
            {"align": "center"},
            {"is_wrapped": True},
            {"align": "center"},
        ],
        "rows": rows[:51],  # Block Kit limit
    }


def _format_ci_source(ci_context):
    """Build a Slack mrkdwn line attributing the sync to a CI run."""
    if not ci_context:
        return None
    parts = []
    repo = ci_context.get("repo")
    sha = ci_context.get("sha")
    run_id = ci_context.get("run_id")
    actor = ci_context.get("actor")

    if repo:
        repo_url = f"https://github.com/{repo}"
        parts.append(f"<{repo_url}|{repo}>")
        if sha:
            parts.append(f"<{repo_url}/commit/{sha}|{sha[:8]}>")
        if run_id:
            parts.append(f"<{repo_url}/actions/runs/{run_id}|run>")

    if not parts:
        return None
    line = f":robot_face: *Triggered by:* {' / '.join(parts)}"
    if actor:
        line += f" ({actor})"
    return line


def notify_slack(results, ci_context=None, ic_reasons=None):
    """Post provisioning summary to Slack.

    Handles two result structures:
    - Hero sync: results contains top-level 'accounts' and 'aliases' keys
      alongside per-group results
    - Team sync: results maps team_name -> {provider: outcome}

    Uses a table for hero account/alias results. Omits sections with no content.
    """
    webhook_url = get_webhook_url(ssm, INFRA_WEBHOOK_PARAM)

    # Detect hero sync results by checking for 'accounts' key
    accounts = results.pop("accounts", None)
    aliases = results.pop("aliases", None)
    is_hero_sync = accounts is not None

    header_text = (
        ":busts_in_silhouette: Hero & Group Sync Complete"
        if is_hero_sync
        else ":busts_in_silhouette: Team Provisioning Complete"
    )

    blocks = [
        {
            "type": "header",
            "text": {"type": "plain_text", "text": header_text, "emoji": True},
        }
    ]

    # Show CI trigger attribution if available
    source_line = _format_ci_source(ci_context)
    if source_line:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": source_line}})

    # Hero-specific: single table for accounts + aliases
    if accounts is not None:
        table = _hero_sync_table(accounts, aliases)
        if table:
            blocks.append(table)

    # Per-group / per-team results
    for team_name, result in results.items():
        lines = [_result_line(p, o) for p, o in result.items()]
        blocks.append(
            {
                "type": "section",
                "text": {
                    "type": "mrkdwn",
                    "text": f"*{team_name}*\n" + "\n".join(lines),
                },
            }
        )

    missing_block = _not_in_identity_center_block(results, ic_reasons)
    if missing_block:
        blocks.append(missing_block)

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    blocks.extend(
        [
            {"type": "divider"},
            {
                "type": "context",
                "elements": [
                    {"type": "mrkdwn", "text": f"Team provisioner | {ts}"}
                ],
            },
        ]
    )

    post_to_slack(
        webhook_url,
        {
            "blocks": blocks,
            "text": (
                f"Hero sync: {', '.join(results.keys())}"
                if is_hero_sync
                else f"Team provisioning: {', '.join(results.keys())}"
            ),
        },
    )


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------

def handler(event, context):
    logger.info("Team provisioner invoked")
    logger.info("Event: %s", json.dumps(event, default=str)[:1000])

    # Dispatch by action
    payload = _extract_payload(event)
    if payload.get("action") == "set_password":
        return handle_set_password(payload)
    if payload.get("action") == "resend_password_link":
        return handle_resend_password_link(payload)
    if payload.get("action") == "sync_groups_and_heros":
        return handle_sync_groups_and_heros(payload)
    if payload.get("action") == "sync_groups":
        return handle_sync_groups(payload)
    if payload.get("action") == "sync_atlassian":
        return handle_sync_atlassian(payload)
    if payload.get("action") == "sync_team_access":
        return handle_sync_team_access(payload)
    if payload.get("action") == "sync_access_members":
        return handle_sync_access_members(payload)

    teams = parse_team_payload(event)
    if not teams:
        logger.info("No team data in event — nothing to provision")
        return {"statusCode": 200, "body": "No teams to provision"}

    results = {}
    provisioned = []

    for team in teams:
        team_name = team.get("name")
        if not team_name:
            logger.warning(
                "Team entry missing 'name' — skipping: %s",
                json.dumps(team)[:200],
            )
            continue

        if not _validate_team_name(team_name):
            results[team_name] = {
                "error": "invalid team name — must be 1-12 lowercase letters only (a-z), "
                         "no hyphens, digits, or special characters"
            }
            continue

        if not team.get("description"):
            logger.warning(
                "Team %s missing required 'description' — skipping",
                team_name,
            )
            results[team_name] = {"error": "missing required 'description' field"}
            continue

        logger.info("Provisioning team: %s", team_name)
        provisioned.append(team)
        tr = {}

        for label, fn in [
            ("google", sync_google_group),
            ("github", sync_github_team),
            ("budget", sync_budget),
            ("cognito", sync_cognito_group),
            ("identity_center", sync_identity_center_group),
        ]:
            try:
                tr[label] = fn(team)
            except Exception as e:
                logger.error("%s sync failed for %s: %s", label, team_name, e)
                tr[label] = {"error": str(e)[:200]}

        results[team_name] = tr

    # Team members reach Identity Center only as direct members of
    # AWS_ACCESS_GROUP (Google SCIM doesn't expand the team group).
    access = None
    if provisioned:
        try:
            access = sync_aws_access_group(provisioned)
        except Exception as e:
            logger.error("AWS access group sync failed: %s", e)
            access = {"error": str(e)[:200]}
    ic_reasons = _explain_not_in_identity_center(results, (access or {}).get("added_members", ()))
    body = {"provisioned": list(results.keys()), "results": results}
    if access is not None:
        body["aws_access_group"] = access
    if ic_reasons:
        body["not_in_identity_center"] = ic_reasons

    try:
        ci_ctx = _extract_payload(event).get("ci_context") if isinstance(event, dict) else None
        slack_results = dict(results)
        if access is not None:
            slack_results[AWS_ACCESS_GROUP] = {"google": access}
        notify_slack(slack_results, ci_context=ci_ctx, ic_reasons=ic_reasons)
    except Exception as e:
        logger.error("Slack notification failed: %s", e)

    return {
        "statusCode": 200,
        "body": json.dumps(body, default=str),
    }
