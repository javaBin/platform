"""Build and post Slack webhook messages for CI alerts.

Usage:
  slack_post.py message <title> <message> [url] [url_label]
  slack_post.py risk <review_result.json> <override_url>

The webhook URL comes from SLACK_WEBHOOK_URL. Text is JSON-escaped and cut to
Slack's block limits (header 150, section 3000). If Slack rejects the blocks
(e.g. invalid_blocks), the message is re-sent as plain text so the alert is
never lost; a non-"ok" reply on the fallback exits 1.
"""

import json
import os
import sys
import urllib.request

HEADER_MAX = 150
SECTION_MAX = 3000
TEXT_MAX = 3900

CATEGORY_EMOJI = {"security": ":lock:", "cost": ":moneybag:", "destruction": ":boom:", "routine": ":white_check_mark:"}
RISK_EMOJI = {"HIGH": ":red_circle:", "FAILED": ":warning:"}


def cut(text, limit):
    text = str(text)
    return text if len(text) <= limit else text[: limit - 1] + "…"


def header(text):
    return {"type": "header", "text": {"type": "plain_text", "text": cut(text, HEADER_MAX), "emoji": True}}


def section(text):
    return {"type": "section", "text": {"type": "mrkdwn", "text": cut(text, SECTION_MAX)}}


def message_payload(title, message, url="", url_label="View"):
    blocks = [header(title), section(message.replace("\\n", "\n"))]
    if url:
        blocks.append(section(f"<{url}|{url_label}>"))
    return {"text": cut(title, TEXT_MAX), "blocks": blocks}


def load_review(path):
    try:
        with open(path) as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def risk_payload(review, override_url, env):
    risk = review.get("risk") or "FAILED"
    if risk not in RISK_EMOJI:
        risk = "HIGH"
    repo = env.get("GITHUB_REPOSITORY", "unknown")
    sha = env.get("GITHUB_SHA", "unknown")
    run_id = env.get("GITHUB_RUN_ID", "")
    actor = env.get("GITHUB_ACTOR", "unknown")
    plan_key = f"plans/{repo}/{run_id}/tfplan"

    if risk == "FAILED":
        title = ":rotating_light: Deploy Blocked — Plan Review Failed"
        summary = review.get("summary") or "The LLM plan review did not return a result, so the plan is treated as HIGH risk."
        footer = ":warning: Review FAILED | Auto-apply blocked — check the plan job log, then override if the plan is safe"
    else:
        title = ":rotating_light: Deploy Blocked — HIGH Risk Plan"
        summary = review.get("summary") or "Review unavailable"
        footer = ":red_circle: Risk HIGH | Auto-apply blocked — override required"

    if run_id and repo != "unknown":
        source = (f":robot_face: <https://github.com/{repo}|{repo}> / <https://github.com/{repo}/commit/{sha}|{sha[:8]}>"
                  f" / <https://github.com/{repo}/actions/runs/{run_id}|run> (triggered by {actor})")
    else:
        source = f":bust_in_silhouette: {repo} / `{sha[:8]}` / {actor}"

    blocks = [header(title), section(source), section(f"*Summary*\n{summary}")]
    findings = [f for f in review.get("findings") or [] if isinstance(f, dict)]
    if findings:
        lines = [f"{CATEGORY_EMOJI.get(f.get('category'), ':black_small_square:')} *[{f.get('category', 'other')}]* {f.get('detail', '')}"
                 for f in findings]
        blocks.append(section("*Findings*\n" + "\n".join(lines)))
    blocks += [
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Plan key*\n`{plan_key}`"},
            {"type": "mrkdwn", "text": f"*Repository*\n`{repo}`"},
            {"type": "mrkdwn", "text": f"*Run ID*\n`{run_id}`"},
        ]},
        {"type": "actions", "elements": [{"type": "button", "text": {"type": "plain_text", "text": ":unlock: Approve Override", "emoji": True},
                                          "url": override_url, "style": "danger"}]},
        {"type": "divider"},
        {"type": "context", "elements": [{"type": "mrkdwn", "text": footer}]},
    ]
    text = (f"{title}: {repo} run {run_id}\nPlan key: {plan_key}\nRun ID: {run_id}\n"
            f"Summary: {summary}\nOverride: {override_url}")
    return {"text": cut(text, TEXT_MAX), "blocks": blocks}


def post(url, payload, opener=urllib.request.urlopen):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with opener(req, timeout=15) as resp:
            return resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.read().decode() or f"http {e.code}"
    except urllib.error.URLError as e:
        return f"error {e.reason}"


def send(url, payload, opener=urllib.request.urlopen):
    """Post blocks; on rejection fall back to plain text. Returns True if Slack said ok."""
    reply = post(url, payload, opener)
    if reply == "ok":
        return True
    print(f"::warning::Slack rejected the block message ({reply}); sending plain text instead.")
    reply = post(url, {"text": payload["text"]}, opener)
    if reply == "ok":
        return True
    print(f"::error::Slack rejected the plain-text message too ({reply}).")
    return False


def main(argv, env=os.environ):
    url = env.get("SLACK_WEBHOOK_URL", "")
    if not url:
        print("No webhook URL available — skipping Slack notification.")
        return 0
    if len(argv) >= 3 and argv[1] == "message":
        payload = message_payload(*argv[2:6])
    elif len(argv) == 4 and argv[1] == "risk":
        payload = risk_payload(load_review(argv[2]), argv[3], env)
    else:
        print(__doc__, file=sys.stderr)
        return 2
    if not send(url, payload):
        return 1
    print(f"Slack notification sent: {payload['text'].splitlines()[0]}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
