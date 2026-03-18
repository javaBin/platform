"""Apply gate — credential broker for Terraform apply.

Actions:
  check   — Verify risk level and override status, return temp credentials if approved
  sign    — Create HMAC-signed override token for a HIGH-risk plan
  status  — Return current risk and override state for a plan

The signing key lives in SSM. Only this Lambda can read it. CI roles invoke
the Lambda but never see the key. Temp credentials are issued via STS
AssumeRole on the team's CI role (resolved from GitHub team membership).
"""

import hashlib
import hmac
import json
import logging
import os
import time

import boto3
from shared.github import resolve_team

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")
s3 = boto3.client("s3")
sts = boto3.client("sts")

SIGNING_KEY_PARAM = os.environ.get(
    "SIGNING_KEY_PARAM", "/javabin/platform/override-signing-key"
)
PLAN_BUCKET = os.environ.get("PLAN_BUCKET", "")
PROJECT = os.environ.get("PROJECT", "javabin")
CREDENTIAL_DURATION = int(os.environ.get("CREDENTIAL_DURATION", "900"))  # 15 min

# Cache signing key across invocations
_key_cache = {}


def _get_signing_key():
    """Read the HMAC signing key from SSM (cached)."""
    if "key" not in _key_cache:
        resp = ssm.get_parameter(Name=SIGNING_KEY_PARAM, WithDecryption=True)
        _key_cache["key"] = resp["Parameter"]["Value"].encode("utf-8")
    return _key_cache["key"]


def _compute_hmac(plan_key):
    """Compute HMAC-SHA256 over the plan key."""
    key = _get_signing_key()
    return hmac.new(key, plan_key.encode("utf-8"), hashlib.sha256).hexdigest()


def _read_s3_json(key):
    """Read a JSON file from the plan bucket. Returns None if not found."""
    try:
        resp = s3.get_object(Bucket=PLAN_BUCKET, Key=key)
        return json.loads(resp["Body"].read())
    except s3.exceptions.NoSuchKey:
        return None
    except Exception as e:
        logger.error("Failed to read s3://%s/%s: %s", PLAN_BUCKET, key, e)
        return None


def _write_s3_json(key, data):
    """Write a JSON file to the plan bucket."""
    s3.put_object(
        Bucket=PLAN_BUCKET,
        Key=key,
        Body=json.dumps(data),
        ContentType="application/json",
    )


def _plan_prefix(plan_key):
    """Get the directory prefix from a plan key (e.g. 'repo/sha/' from 'repo/sha/tfplan')."""
    parts = plan_key.rsplit("/", 1)
    return parts[0] + "/" if len(parts) > 1 else ""


def action_check(event):
    """Check risk + override, return temp credentials if approved.

    Input: {plan_key, repo_name}
    Output: {approved, risk_level, credentials?, reason?}
    """
    plan_key = event["plan_key"]
    repo_name = event["repo_name"]
    prefix = _plan_prefix(plan_key)

    # Read risk assessment
    risk = _read_s3_json(f"{prefix}risk.json")
    if not risk:
        return {
            "approved": False,
            "reason": "No risk assessment found for this plan",
        }

    risk_level = risk.get("level", "UNKNOWN")
    logger.info("Plan %s risk: %s", plan_key, risk_level)

    # LOW/MEDIUM — auto-approve
    if risk_level in ("LOW", "MEDIUM"):
        credentials = _issue_credentials(repo_name)
        return {
            "approved": True,
            "risk_level": risk_level,
            "credentials": credentials,
        }

    # HIGH/FAILED — check for override
    override = _read_s3_json(f"{prefix}override.json")
    if not override:
        return {
            "approved": False,
            "risk_level": risk_level,
            "reason": f"Risk is {risk_level} — override required",
        }

    # Verify HMAC signature
    expected = _compute_hmac(plan_key)
    actual = override.get("signature", "")

    if not hmac.compare_digest(expected, actual):
        logger.warning("Invalid override signature for %s", plan_key)
        return {
            "approved": False,
            "risk_level": risk_level,
            "reason": "Override signature is invalid",
        }

    logger.info(
        "Override verified for %s (approved by %s)",
        plan_key, override.get("approved_by"),
    )

    credentials = _issue_credentials(repo_name)
    return {
        "approved": True,
        "risk_level": risk_level,
        "override": {
            "approved_by": override.get("approved_by"),
            "reason": override.get("reason"),
        },
        "credentials": credentials,
    }


def action_sign(event):
    """Create a signed override token and write it to S3.

    Input: {plan_key, approved_by, reason}
    Output: {signed: true, plan_key}
    """
    plan_key = event["plan_key"]
    approved_by = event["approved_by"]
    reason = event.get("reason", "")
    prefix = _plan_prefix(plan_key)

    signature = _compute_hmac(plan_key)

    override = {
        "signature": signature,
        "plan_key": plan_key,
        "approved_by": approved_by,
        "reason": reason,
        "approved_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }

    _write_s3_json(f"{prefix}override.json", override)
    logger.info("Override signed for %s by %s", plan_key, approved_by)

    return {"signed": True, "plan_key": plan_key}


def action_status(event):
    """Return current risk and override state for a plan.

    Input: {plan_key}
    Output: {risk_level, has_override, override_valid}
    """
    plan_key = event["plan_key"]
    prefix = _plan_prefix(plan_key)

    risk = _read_s3_json(f"{prefix}risk.json")
    risk_level = risk.get("level", "UNKNOWN") if risk else "NOT_FOUND"

    override = _read_s3_json(f"{prefix}override.json")
    has_override = override is not None
    override_valid = False

    if has_override:
        expected = _compute_hmac(plan_key)
        override_valid = hmac.compare_digest(
            expected, override.get("signature", "")
        )

    return {
        "risk_level": risk_level,
        "has_override": has_override,
        "override_valid": override_valid,
    }


def _issue_credentials(repo_name):
    """Resolve team from GitHub and assume the team's CI role."""
    account_id = os.environ.get("ACCOUNT_ID", "")

    team = resolve_team(repo_name)
    if not team:
        raise RuntimeError(
            f"Repo '{repo_name}' is not in any GitHub team. "
            f"Add it at https://github.com/orgs/javaBin/teams"
        )

    role_arn = f"arn:aws:iam::{account_id}:role/{PROJECT}-ci-team-{team}"
    logger.info("Assuming team role %s for repo %s", role_arn, repo_name)

    resp = sts.assume_role(
        RoleArn=role_arn,
        RoleSessionName=f"apply-gate-{repo_name}",
        DurationSeconds=CREDENTIAL_DURATION,
    )

    creds = resp["Credentials"]
    return {
        "AccessKeyId": creds["AccessKeyId"],
        "SecretAccessKey": creds["SecretAccessKey"],
        "SessionToken": creds["SessionToken"],
    }


def handler(event, context):
    logger.info("Apply gate invoked: %s", json.dumps(event, default=str)[:500])

    action = event.get("action", "check")

    try:
        if action == "check":
            result = action_check(event)
        elif action == "sign":
            result = action_sign(event)
        elif action == "status":
            result = action_status(event)
        else:
            result = {"error": f"Unknown action: {action}"}

        return {"statusCode": 200, "body": json.dumps(result, default=str)}

    except Exception as e:
        logger.error("Apply gate error: %s", e, exc_info=True)
        return {
            "statusCode": 500,
            "body": json.dumps({"error": str(e)}),
        }
