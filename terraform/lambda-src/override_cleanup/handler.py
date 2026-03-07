"""Override cleanup — deletes stale SSM override parameters.

Risk gate overrides are stored as SSM parameters under /javabin/platform-overrides/{repo}/{sha}.
They are single-use tokens that should be cleaned up after 24 hours to prevent
accumulation of stale approvals.

Trigger: EventBridge (hourly schedule)
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

OVERRIDE_PATH = os.environ.get("OVERRIDE_PATH", "/javabin/platform-overrides/")
MAX_AGE_HOURS = int(os.environ.get("MAX_AGE_HOURS", "24"))


def handler(event, context):
    cutoff = datetime.now(timezone.utc) - timedelta(hours=MAX_AGE_HOURS)
    deleted = 0
    next_token = None

    while True:
        kwargs = {
            "Path": OVERRIDE_PATH,
            "Recursive": True,
            "MaxResults": 10,
        }
        if next_token:
            kwargs["NextToken"] = next_token

        resp = ssm.get_parameters_by_path(**kwargs)
        parameters = resp.get("Parameters", [])

        for param in parameters:
            last_modified = param.get("LastModifiedDate")
            if last_modified and last_modified < cutoff:
                param_name = param["Name"]
                try:
                    ssm.delete_parameter(Name=param_name)
                    deleted += 1
                    logger.info("Deleted stale override: %s (modified %s)", param_name, last_modified)
                except Exception as e:
                    logger.error("Failed to delete %s: %s", param_name, e)

        next_token = resp.get("NextToken")
        if not next_token:
            break

    logger.info("Override cleanup complete: %d stale parameter(s) deleted", deleted)
    return {"statusCode": 200, "body": f"Deleted {deleted} stale overrides"}
