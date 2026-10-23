"""Plan review — LLM risk review of an app's Terraform plan, on platform credentials.

tf-plan runs with the team's CI role, which the developer boundary keeps away
from Bedrock (and must: any code in a plan job runs with those credentials).
So app plans are reviewed here instead:

  1. Verify the caller's GitHub OIDC token; the repo comes from its signed
     claims, and the plan must be that repo's own (plans/javaBin/<repo>/...).
  2. Read plan-output.txt next to the plan in the plan bucket.
  3. Review it with Bedrock (review_plan.py, shared with platform CI),
     retrying transient failures.
  4. Write risk.json (read by the apply gate) and review.json. Team roles
     cannot write these objects. (The plan text itself is uploaded by the
     plan job, so the review guards against mistakes, not against code in
     the job forging its own plan text.)
  5. For HIGH or FAILED on main, post the override request to Slack.

Input:  {plan_key, id_token, notify?}
Output: {risk, markdown} or {error}
"""

import json
import logging
import os
import time

import boto3
from botocore.config import Config

import review_plan
from shared.github_oidc import TokenError, repo_name, verify
from shared.slack import get_webhook_url, post_to_slack

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

PROJECT = os.environ.get("PROJECT", "javabin")
GITHUB_ORG = os.environ.get("GITHUB_ORG", "javaBin")
PLAN_BUCKET = os.environ["PLAN_BUCKET"]
OIDC_AUDIENCE = os.environ.get("OIDC_AUDIENCE", f"{PROJECT}-plan-review")
SLACK_PARAM = os.environ.get("SLACK_WEBHOOK_PARAM", f"/{PROJECT}/slack/platform-override-alerts-webhook")
OVERRIDE_URL = f"https://github.com/{GITHUB_ORG}/platform/actions/workflows/approve-override.yml"
ATTEMPTS = int(os.environ.get("REVIEW_ATTEMPTS", "3"))

s3 = boto3.client("s3")
ssm = boto3.client("ssm")
# Bounded per call, so retries fit inside the Lambda timeout; the handler
# does its own retrying.
bedrock = boto3.client("bedrock-runtime", config=Config(read_timeout=150, connect_timeout=10,
                                                         retries={"max_attempts": 1, "mode": "standard"}))
CALL_BUDGET_MS = 160_000


def converse(messages, tool_config):
    return bedrock.converse(modelId=review_plan.MODEL_ID, messages=messages, toolConfig=tool_config)


def review_with_retries(plan_text, context=None):
    for attempt in range(1, ATTEMPTS + 1):
        if context and context.get_remaining_time_in_millis() < CALL_BUDGET_MS:
            logger.warning("Stopping after %d attempts: not enough time left for another", attempt - 1)
            return None
        try:
            review = review_plan.review_plan(plan_text, converse=converse)
            if review:
                return review
            logger.warning("Review attempt %d returned no structured output", attempt)
        except Exception as e:  # throttling, timeouts, model errors
            logger.warning("Review attempt %d failed: %s", attempt, e)
        if attempt < ATTEMPTS:
            time.sleep(2 ** attempt)
    return None


def put_json(key, body):
    s3.put_object(Bucket=PLAN_BUCKET, Key=key, Body=json.dumps(body).encode(), ContentType="application/json")


def notify(repo, plan_key, risk, review):
    summary = review.get("summary", "") if review else "The LLM review could not run; re-run the workflow or request an override."
    text = (f"*Apply blocked — {risk} risk*\n*Repo:* {GITHUB_ORG}/{repo}\n*Plan:* `{plan_key}`\n{summary}\n"
            f"<{OVERRIDE_URL}|Approve override>")
    try:
        post_to_slack(get_webhook_url(ssm, SLACK_PARAM), {"text": text})
    except Exception as e:
        logger.error("Slack notification failed: %s", e)


def handler(event, context):
    try:
        repo = repo_name(verify(event.get("id_token"), OIDC_AUDIENCE))
    except TokenError as e:
        logger.warning("Rejected review call: %s", e)
        return {"error": f"Invalid GitHub OIDC token: {e}"}

    plan_key = event.get("plan_key", "")
    prefix = f"plans/{GITHUB_ORG}/{repo}/"
    if not plan_key.startswith(prefix) or not plan_key.endswith("/tfplan") or ".." in plan_key:
        return {"error": f"Plan {plan_key!r} does not belong to {repo}"}
    plan_dir = plan_key.rsplit("/", 1)[0]

    # Record FAILED first: if anything below crashes or times out, the gate
    # still finds a verdict (and a board override can unblock it).
    put_json(f"{plan_dir}/risk.json", {"level": "FAILED", "reviewed_at": None, "reviewer": f"{PROJECT}-plan-review"})

    plan_text = s3.get_object(Bucket=PLAN_BUCKET, Key=f"{plan_dir}/plan-output.txt")["Body"].read().decode()
    if not plan_text.strip():
        review = {"risk": "LOW", "summary": "Empty plan", "findings": []}
    else:
        review = review_with_retries(plan_text, context)

    risk = review["risk"] if review else "FAILED"
    reviewed_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    put_json(f"{plan_dir}/risk.json", {"level": risk, "reviewed_at": reviewed_at, "reviewer": f"{PROJECT}-plan-review"})
    put_json(f"{plan_dir}/review.json", review or {"risk": "FAILED"})
    logger.info("Reviewed %s: %s", plan_key, risk)

    if risk in ("HIGH", "FAILED") and event.get("notify"):
        notify(repo, plan_key, risk, review)

    markdown = review_plan.format_review(review) if review else (
        "**Risk: ⚪ FAILED**\n\nThe LLM review could not run after "
        f"{ATTEMPTS} attempts. Re-run the workflow; if it keeps failing, request an override.")
    return {"risk": risk, "markdown": markdown}
