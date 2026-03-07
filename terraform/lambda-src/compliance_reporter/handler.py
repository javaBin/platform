"""Compliance reporter — detects untagged resources and reports to Slack.

Unlike the reference auto_tagger, this Lambda does NOT auto-tag resources.
Tags are applied at creation via CI/CD. This reporter catches leaks and
posts to Slack so the team can investigate.

Trigger: EventBridge (Create events via CloudTrail)
"""

import json
import logging
import os
import urllib.request
from datetime import datetime, timezone

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

PROJECT_PREFIX = os.environ.get("PROJECT_PREFIX", "javabin")
INFRA_WEBHOOK_PARAM = os.environ["INFRA_WEBHOOK_PARAM"]
ALLOWED_IDENTITIES = [
    s.strip()
    for s in os.environ.get("ALLOWED_IDENTITIES", "").split(",")
    if s.strip()
]

REQUIRED_TAGS = {"project", "team", "managed-by"}

# AWS clients
ec2 = boto3.client("ec2")
s3 = boto3.client("s3")
ecs = boto3.client("ecs")
elbv2 = boto3.client("elbv2")
lambda_client = boto3.client("lambda")
rds = boto3.client("rds")

_webhook_cache = {}


def get_webhook_url(param_name):
    if param_name not in _webhook_cache:
        resp = ssm.get_parameter(Name=param_name, WithDecryption=True)
        _webhook_cache[param_name] = resp["Parameter"]["Value"]
    return _webhook_cache[param_name]


def is_allowed_caller(caller_arn):
    """Only check resources created by our known roles/users."""
    caller_lower = caller_arn.lower()
    return any(identity in caller_lower for identity in ALLOWED_IDENTITIES)


def handler(event, context):
    detail = event.get("detail", {})
    event_name = detail.get("eventName", "")
    event_source = detail.get("eventSource", "")

    identity = detail.get("userIdentity", {})
    caller_arn = identity.get("arn", "")

    # Skip events from our own role to prevent loops
    if "compliance-reporter" in caller_arn:
        return

    # Only check resources created by our known roles/users
    if not is_allowed_caller(caller_arn):
        logger.info("Skipping %s — caller %s not in allowed list", event_name, caller_arn)
        return

    resources = extract_resources(event_name, event_source, detail)

    if not resources:
        logger.info("No taggable resources for %s from %s", event_name, event_source)
        return

    untagged = []
    for resource in resources:
        missing = check_tags(resource)
        if missing:
            untagged.append({"resource": resource, "missing": missing})

    if untagged:
        report_to_slack(untagged, event_name, caller_arn)


def extract_resources(event_name, event_source, detail):
    """Extract taggable resource identifiers from CloudTrail event."""
    response = detail.get("responseElements") or {}
    request = detail.get("requestParameters") or {}
    resources = []

    if event_source == "ec2.amazonaws.com":
        if event_name == "RunInstances":
            for inst in response.get("instancesSet", {}).get("items", []):
                iid = inst.get("instanceId")
                if iid:
                    resources.append({"type": "ec2", "id": iid})
        elif event_name == "CreateSecurityGroup":
            gid = response.get("groupId")
            if gid:
                resources.append({"type": "ec2", "id": gid})
        elif event_name == "CreateNatGateway":
            nat = response.get("CreateNatGatewayResponse", response)
            nat_id = nat.get("natGateway", {}).get("natGatewayId")
            if nat_id:
                resources.append({"type": "ec2", "id": nat_id})
        elif event_name == "CreateVpc":
            vpc_id = response.get("vpc", {}).get("vpcId")
            if vpc_id:
                resources.append({"type": "ec2", "id": vpc_id})
        elif event_name == "CreateSubnet":
            subnet_id = response.get("subnet", {}).get("subnetId")
            if subnet_id:
                resources.append({"type": "ec2", "id": subnet_id})

    elif event_source == "s3.amazonaws.com":
        if event_name == "CreateBucket":
            bucket = request.get("bucketName")
            if bucket:
                resources.append({"type": "s3", "id": bucket})

    elif event_source == "ecs.amazonaws.com":
        if event_name == "CreateCluster":
            arn = response.get("cluster", {}).get("clusterArn")
            if arn:
                resources.append({"type": "ecs", "id": arn})
        elif event_name == "CreateService":
            arn = response.get("service", {}).get("serviceArn")
            if arn:
                resources.append({"type": "ecs", "id": arn})

    elif event_source == "elasticloadbalancing.amazonaws.com":
        if event_name == "CreateLoadBalancer":
            for lb in response.get("loadBalancers", []):
                arn = lb.get("loadBalancerArn")
                if arn:
                    resources.append({"type": "elbv2", "id": arn})
        elif event_name == "CreateTargetGroup":
            for tg in response.get("targetGroups", []):
                arn = tg.get("targetGroupArn")
                if arn:
                    resources.append({"type": "elbv2", "id": arn})

    elif event_source == "lambda.amazonaws.com":
        if event_name == "CreateFunction20150331":
            arn = response.get("functionArn")
            if arn:
                resources.append({"type": "lambda", "id": arn})

    elif event_source == "rds.amazonaws.com":
        if event_name == "CreateDBInstance":
            arn = response.get("dBInstanceArn")
            if arn:
                resources.append({"type": "rds", "id": arn})

    return resources


def get_existing_tags(resource):
    """Read existing tags for a resource. Returns dict of key->value."""
    rtype = resource["type"]
    rid = resource["id"]

    try:
        if rtype == "ec2":
            resp = ec2.describe_tags(
                Filters=[{"Name": "resource-id", "Values": [rid]}]
            )
            return {t["Key"]: t["Value"] for t in resp.get("Tags", [])}

        elif rtype == "s3":
            resp = s3.get_bucket_tagging(Bucket=rid)
            return {t["Key"]: t["Value"] for t in resp.get("TagSet", [])}

        elif rtype == "ecs":
            resp = ecs.list_tags_for_resource(resourceArn=rid)
            return {t["key"]: t["value"] for t in resp.get("tags", [])}

        elif rtype == "elbv2":
            resp = elbv2.describe_tags(ResourceArns=[rid])
            descs = resp.get("TagDescriptions", [])
            if descs:
                return {t["Key"]: t["Value"] for t in descs[0].get("Tags", [])}

        elif rtype == "lambda":
            resp = lambda_client.list_tags(Resource=rid)
            return resp.get("Tags", {})

        elif rtype == "rds":
            resp = rds.list_tags_for_resource(ResourceName=rid)
            return {t["Key"]: t["Value"] for t in resp.get("TagList", [])}

    except Exception as e:
        error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if error_code == "NoSuchTagSet":
            return {}
        logger.warning("Could not read tags for %s:%s: %s", rtype, rid, e)

    return {}


def check_tags(resource):
    """Return set of missing required tags, or empty set if compliant."""
    existing = get_existing_tags(resource)
    return REQUIRED_TAGS - set(existing.keys())


def report_to_slack(untagged_resources, event_name, caller_arn):
    """Post a compliance report to Slack."""
    resource_lines = []
    for item in untagged_resources:
        res = item["resource"]
        missing = item["missing"]
        resource_lines.append(
            f"  * `{res['type']}:{res['id']}` — missing: {', '.join(sorted(missing))}"
        )

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": ":red_circle: Untagged Resources Detected", "emoji": True}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": f"*{len(untagged_resources)}* resource(s) created by `{event_name}` "
                          f"are missing required tags (`project`, `team`, `managed-by`)."}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": "\n".join(resource_lines)}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": f"*Created by:* `{caller_arn}`\n"
                          "_Tags should be applied via CI/CD. Investigate the deploying workflow._"}},
        {"type": "divider"},
        {"type": "context",
         "elements": [{"type": "mrkdwn",
                        "text": f"Source: javabin compliance-reporter | "
                                f"{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}"}]},
    ]

    fallback = f"Untagged resources: {len(untagged_resources)} found after {event_name}"

    webhook_url = get_webhook_url(INFRA_WEBHOOK_PARAM)
    payload = json.dumps({"blocks": blocks, "text": fallback}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        logger.error("Failed to post compliance report to Slack: %s", e)
