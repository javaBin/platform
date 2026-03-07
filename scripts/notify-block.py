#!/usr/bin/env python3
"""Post a deploy-blocked notification to Slack.

Usage:
  python3 scripts/notify-block.py

Required environment variables:
  WEBHOOK_URL  - Slack webhook URL
  RISK         - Risk level (HIGH, FAILED, etc.)
  GITHUB_REPOSITORY - repo name
  GITHUB_SHA   - commit SHA
  GITHUB_ACTOR - who triggered the run
  OVERRIDE_URL - link to approve-override workflow
"""

import json
import os
import sys
import urllib.request


def main():
    webhook = os.environ.get("WEBHOOK_URL", "")
    if not webhook:
        print("No WEBHOOK_URL set, skipping Slack notification")
        return

    risk = os.environ.get("RISK", "UNKNOWN")
    repo = os.environ.get("GITHUB_REPOSITORY", "unknown")
    sha = os.environ.get("GITHUB_SHA", "unknown")[:8]
    actor = os.environ.get("GITHUB_ACTOR", "unknown")
    override_url = os.environ.get("OVERRIDE_URL", "")

    title = f"Deploy Blocked — {risk} Risk"

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": title}},
        {
            "type": "section",
            "text": {
                "type": "mrkdwn",
                "text": f"*Repo:* {repo}\n*SHA:* `{sha}`\n*Actor:* {actor}",
            },
        },
    ]

    if override_url:
        blocks.append(
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": f"<{override_url}|Approve Override>"},
            }
        )

    payload = json.dumps({"blocks": blocks, "text": f"Deploy blocked for {repo}"}).encode()
    req = urllib.request.Request(webhook, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req)
        print(f"Slack notification sent: {title}")
    except Exception as e:
        print(f"Failed to send Slack notification: {e}", file=sys.stderr)


if __name__ == "__main__":
    main()
