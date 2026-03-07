"""Shared Slack utilities for Lambda functions."""

import json
import logging
import urllib.request

logger = logging.getLogger(__name__)

_webhook_cache = {}


def get_webhook_url(ssm_client, param_name):
    """Fetch and cache a Slack webhook URL from SSM."""
    if param_name not in _webhook_cache:
        resp = ssm_client.get_parameter(Name=param_name, WithDecryption=True)
        _webhook_cache[param_name] = resp["Parameter"]["Value"]
    return _webhook_cache[param_name]


def post_to_slack(webhook_url, message):
    """Post a Block Kit message to Slack."""
    payload = json.dumps(message).encode("utf-8")
    req = urllib.request.Request(
        webhook_url,
        data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        logger.error("Failed to post to Slack: %s", e)
