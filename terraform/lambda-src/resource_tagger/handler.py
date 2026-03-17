"""Resource tagger — auto-tags newly created AWS resources with creator attribution.

Receives CloudTrail events via EventBridge (same trigger pattern as compliance-reporter).
Tags resources with `created-by` and `commit` extracted from the IAM session name.

For CI users: session name = {actor}-{sha8}-{run_id} -> created-by={actor}, commit={sha8}
For console users (Identity Center): created-by from principalId/username, no commit tag.

Best-effort: never fails the Lambda. Logs warnings for unhandled event types.
"""

import json
import logging
import os
import re

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

AWS_ACCOUNT_ID = os.environ.get("AWS_ACCOUNT_ID", "")
REGION = os.environ.get("AWS_REGION", "eu-central-1")

tagging_client = boto3.client("resourcegroupstaggingapi")


def handler(event, context):
    """Main entry point — EventBridge CloudTrail event."""
    detail = event.get("detail", {})
    event_name = detail.get("eventName", "")
    event_source = detail.get("eventSource", "")

    identity = detail.get("userIdentity", {})
    caller_arn = identity.get("arn", "")

    # Skip events from our own role to prevent loops
    if "resource-tagger" in caller_arn:
        return

    # Extract resource ARNs from the event
    arns = extract_resource_arns(event_name, event_source, detail)
    if not arns:
        logger.info("No taggable ARNs for %s from %s", event_name, event_source)
        return

    # Parse creator identity from the session
    created_by, commit = parse_session_identity(identity)
    if not created_by:
        logger.info("Could not determine creator for %s", event_name)
        return

    # Build tags to apply
    tags = {"created-by": created_by}
    if commit:
        tags["commit"] = commit

    # Tag each resource, skipping those that already have created-by
    for arn in arns:
        tag_resource(arn, tags)


def parse_session_identity(identity):
    """Extract creator and commit from CloudTrail userIdentity.

    Returns (created_by, commit) where commit may be None.
    """
    identity_type = identity.get("type", "")
    arn = identity.get("arn", "unknown")

    if identity_type == "AssumedRole":
        parts = arn.split("/")
        role_name = parts[1] if len(parts) > 1 else "unknown"
        session = parts[2] if len(parts) > 2 else ""

        # Check if this is a GitHub Actions CI session
        is_ci = "javabin-ci-" in role_name.lower()

        if is_ci and session:
            # Parse enriched session name: {actor}-{sha8}-{run_id}
            # Actor may contain hyphens, sha8 is 8 hex chars, run_id is digits
            enriched_match = re.match(r"^(.+)-([0-9a-f]{8})-(\d+)$", session)
            if enriched_match:
                actor = enriched_match.group(1)
                sha8 = enriched_match.group(2)
                return actor, sha8

            # Fallback: use session name as actor, no commit
            return session, None

        # Non-CI assumed role (Identity Center, etc.)
        # Use role_name + session for attribution
        if session:
            return f"{role_name}/{session}", None
        return role_name, None

    elif identity_type == "IAMUser":
        username = identity.get("userName", arn.split("/")[-1])
        return username, None

    elif identity_type == "Root":
        return "root", None

    # Federated or unknown
    principal_id = identity.get("principalId", "")
    if principal_id:
        return principal_id, None

    return None, None


def extract_resource_arns(event_name, event_source, detail):
    """Extract resource ARNs from a CloudTrail event.

    Tries multiple extraction strategies in order of reliability:
    1. detail.resources[].ARN — EventBridge-enriched ARN
    2. responseElements — service-specific ARN fields
    3. requestParameters — build ARN from identifiers (EC2, S3)

    Returns a list of ARN strings.
    """
    arns = []

    # Strategy 1: EventBridge-enriched resources array
    resources = detail.get("resources", [])
    for res in resources:
        arn = res.get("ARN") or res.get("arn")
        if arn:
            arns.append(arn)
    if arns:
        return arns

    # Strategy 2: responseElements — common ARN fields
    response = detail.get("responseElements") or {}
    request = detail.get("requestParameters") or {}

    if event_source == "ec2.amazonaws.com":
        arns.extend(_extract_ec2_arns(event_name, response))

    elif event_source == "s3.amazonaws.com":
        if event_name == "CreateBucket":
            bucket = request.get("bucketName")
            if bucket:
                arns.append(f"arn:aws:s3:::{bucket}")

    elif event_source == "rds.amazonaws.com":
        arn = response.get("dBInstanceArn")
        if arn:
            arns.append(arn)
        # CreateDBCluster
        arn = response.get("dBClusterArn")
        if arn:
            arns.append(arn)

    elif event_source == "ecs.amazonaws.com":
        arns.extend(_extract_ecs_arns(event_name, response))

    elif event_source == "elasticloadbalancing.amazonaws.com":
        arns.extend(_extract_elb_arns(event_name, response))

    elif event_source == "lambda.amazonaws.com":
        arn = response.get("functionArn")
        if arn:
            arns.append(arn)

    if not arns:
        # Strategy 3: scan responseElements for any field ending in "Arn" or "ARN"
        arns.extend(_scan_for_arns(response))

    return arns


def _extract_ec2_arns(event_name, response):
    """Extract ARNs from EC2 CloudTrail response."""
    arns = []
    if event_name == "RunInstances":
        for inst in response.get("instancesSet", {}).get("items", []):
            iid = inst.get("instanceId")
            if iid:
                arns.append(
                    f"arn:aws:ec2:{REGION}:{AWS_ACCOUNT_ID}:instance/{iid}"
                )
    elif event_name == "CreateSecurityGroup":
        gid = response.get("groupId")
        if gid:
            arns.append(
                f"arn:aws:ec2:{REGION}:{AWS_ACCOUNT_ID}:security-group/{gid}"
            )
    elif event_name == "CreateNatGateway":
        nat = response.get("CreateNatGatewayResponse", response)
        nat_id = nat.get("natGateway", {}).get("natGatewayId")
        if nat_id:
            arns.append(
                f"arn:aws:ec2:{REGION}:{AWS_ACCOUNT_ID}:natgateway/{nat_id}"
            )
    elif event_name == "CreateVpc":
        vpc_id = response.get("vpc", {}).get("vpcId")
        if vpc_id:
            arns.append(
                f"arn:aws:ec2:{REGION}:{AWS_ACCOUNT_ID}:vpc/{vpc_id}"
            )
    elif event_name == "CreateSubnet":
        subnet_id = response.get("subnet", {}).get("subnetId")
        if subnet_id:
            arns.append(
                f"arn:aws:ec2:{REGION}:{AWS_ACCOUNT_ID}:subnet/{subnet_id}"
            )
    return arns


def _extract_ecs_arns(event_name, response):
    """Extract ARNs from ECS CloudTrail response."""
    arns = []
    if event_name == "CreateCluster":
        arn = response.get("cluster", {}).get("clusterArn")
        if arn:
            arns.append(arn)
    elif event_name == "CreateService":
        arn = response.get("service", {}).get("serviceArn")
        if arn:
            arns.append(arn)
    elif event_name == "RunTask":
        for task in response.get("tasks", []):
            arn = task.get("taskArn")
            if arn:
                arns.append(arn)
    elif event_name == "CreateTaskSet":
        arn = response.get("taskSet", {}).get("taskSetArn")
        if arn:
            arns.append(arn)
    return arns


def _extract_elb_arns(event_name, response):
    """Extract ARNs from ELB CloudTrail response."""
    arns = []
    if event_name == "CreateLoadBalancer":
        for lb in response.get("loadBalancers", []):
            arn = lb.get("loadBalancerArn")
            if arn:
                arns.append(arn)
    elif event_name == "CreateTargetGroup":
        for tg in response.get("targetGroups", []):
            arn = tg.get("targetGroupArn")
            if arn:
                arns.append(arn)
    return arns


def _scan_for_arns(obj, depth=0):
    """Recursively scan a dict for values that look like ARNs.

    Limits depth to avoid excessive recursion on large events.
    """
    if depth > 4:
        return []

    arns = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            if isinstance(value, str) and value.startswith("arn:aws:"):
                arns.append(value)
            elif isinstance(value, (dict, list)):
                arns.extend(_scan_for_arns(value, depth + 1))
    elif isinstance(obj, list):
        for item in obj:
            arns.extend(_scan_for_arns(item, depth + 1))
    return arns


def tag_resource(arn, tags):
    """Tag a single resource, checking for existing created-by tag first.

    Uses the Resource Groups Tagging API for uniform cross-service tagging.
    Skips if created-by already exists (preserves original creator).
    """
    try:
        # Check existing tags
        resp = tagging_client.get_resources(
            ResourceARNList=[arn],
            ResourcesPerPage=1,
        )
        resource_list = resp.get("ResourceTagMappingList", [])
        if resource_list:
            existing_tags = {
                t["Key"]: t["Value"]
                for t in resource_list[0].get("Tags", [])
            }
            if "created-by" in existing_tags:
                logger.info(
                    "Skipping %s — already has created-by=%s",
                    arn,
                    existing_tags["created-by"],
                )
                return

        # Apply tags
        tagging_client.tag_resources(
            ResourceARNList=[arn],
            Tags=tags,
        )
        logger.info("Tagged %s with %s", arn, json.dumps(tags))

    except tagging_client.exceptions.InvalidParameterException as e:
        logger.warning("Cannot tag %s (unsupported resource type): %s", arn, e)
    except Exception as e:
        # Best effort — never fail the Lambda
        error_code = getattr(e, "response", {}).get("Error", {}).get("Code", "")
        if error_code in ("InternalServiceException", "ThrottlingException"):
            logger.warning("Transient error tagging %s: %s", arn, e)
        else:
            logger.warning("Failed to tag %s: %s", arn, e)
