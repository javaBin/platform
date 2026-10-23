import hashlib
import json
import logging
import os
import re
import time
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from shared.constants import risk_emoji
from shared.pricing import estimate_cost
from shared.slack import get_webhook_url, post_to_slack

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")
dynamodb = boto3.resource("dynamodb")

# SSM parameter names passed via environment
INFRA_WEBHOOK_PARAM = os.environ["INFRA_WEBHOOK_PARAM"]
COST_WEBHOOK_PARAM = os.environ["COST_WEBHOOK_PARAM"]
SECURITY_WEBHOOK_PARAM = os.environ.get("SECURITY_WEBHOOK_PARAM", "")
SECURITY_TOPIC_ARN = os.environ["SECURITY_TOPIC_ARN"]
PROJECT_PREFIX = os.environ.get("PROJECT_PREFIX", "javabin")
GITHUB_ORG_URL = os.environ.get("GITHUB_ORG_URL", "https://github.com/javaBin")
DEDUP_TABLE_NAME = os.environ.get("DEDUP_TABLE_NAME", "javabin-alert-dedup")

# Dedup cache: "{action}:{resource}" -> timestamp
_recent_alerts = {}
DEDUP_WINDOW = 300  # 5 minutes
DEDUP_TTL_DAYS = 30

# CI activity tracker: resource_name -> timestamp
# Suppresses SecurityHub/GuardDuty findings for resources actively managed by CI
_recent_ci_resources = {}
CI_SUPPRESS_WINDOW = 600  # 10 minutes


# ---------------------------------------------------------------------------
# DynamoDB dedup — persistent dedup for Security Hub + compliance alerts
# ---------------------------------------------------------------------------
def _dedup_table():
    return dynamodb.Table(DEDUP_TABLE_NAME)


def _finding_dedup_key(finding):
    """Build a dedup key from a Security Hub finding."""
    resources = finding.get("Resources", [])
    resource_arn = resources[0].get("Id", "unknown") if resources else "unknown"
    title_hash = hashlib.sha256(finding.get("Title", "").encode()).hexdigest()[:12]
    return f"{resource_arn}:{title_hash}"


def is_finding_already_alerted(dedup_key):
    """Check DynamoDB for an existing alert entry."""
    try:
        resp = _dedup_table().get_item(Key={"finding_key": dedup_key})
        return "Item" in resp
    except Exception as e:
        logger.warning("DynamoDB dedup check failed (allowing alert): %s", e)
        return False


def record_finding_alert(dedup_key):
    """Write dedup entry with TTL."""
    try:
        expires_at = int(time.time()) + (DEDUP_TTL_DAYS * 86400)
        _dedup_table().put_item(Item={
            "finding_key": dedup_key,
            "alerted_at": datetime.now(timezone.utc).isoformat(),
            "expires_at": expires_at,
        })
    except Exception as e:
        logger.warning("DynamoDB dedup write failed: %s", e)

# ---------------------------------------------------------------------------
# CI resource suppression — track resources managed by CI, suppress noisy findings
# ---------------------------------------------------------------------------
def _track_ci_resource(resource_name):
    """Record a resource being actively managed by CI."""
    if not resource_name:
        return
    now = time.time()
    for k in list(_recent_ci_resources):
        if now - _recent_ci_resources[k] > CI_SUPPRESS_WINDOW:
            del _recent_ci_resources[k]
    _recent_ci_resources[resource_name] = now


def _is_ci_managed_resource(resource_name):
    """Check if a resource was recently managed by CI (within suppress window)."""
    now = time.time()
    for k in list(_recent_ci_resources):
        if now - _recent_ci_resources[k] > CI_SUPPRESS_WINDOW:
            del _recent_ci_resources[k]
    return resource_name in _recent_ci_resources if resource_name else False


# ---------------------------------------------------------------------------
# Console URL builder — derives service from eventSource, minimal overrides
# ---------------------------------------------------------------------------
_CONSOLE_OVERRIDES = {
    "s3": lambda rid, r, **kw: f"https://s3.console.aws.amazon.com/s3/buckets/{rid}?region={r}",
    "iam": lambda rid, r, **kw: f"https://us-east-1.console.aws.amazon.com/iam/home#/roles/{rid}",
    "ecs": lambda rid, r, **kw: f"https://{r}.console.aws.amazon.com/ecs/v2/clusters/{kw.get('cluster', 'default')}/services/{rid}",
}

# ---------------------------------------------------------------------------
# LLM analysis via Bedrock — structured output
# ---------------------------------------------------------------------------
DEPLOY_REGION = os.environ.get("DEPLOY_REGION", "eu-central-1")
LLM_MODEL_ID = "eu.anthropic.claude-haiku-5-5"

_SECURITY_TOOL = {
    "tools": [{
        "toolSpec": {
            "name": "submit_analysis",
            "description": "Submit a security analysis of an AWS change",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "risk": {
                            "type": "string",
                            "enum": ["LOW", "MEDIUM", "HIGH"],
                        },
                        "should_alert": {
                            "type": "boolean",
                            "description": "True if this deserves human attention. "
                            "False for routine CI/CD operations with no security concern.",
                        },
                        "analysis": {
                            "type": "string",
                            "description": "1-2 sentence explanation of the risk assessment.",
                        },
                    },
                    "required": ["risk", "should_alert", "analysis"],
                }
            },
        }
    }],
    "toolChoice": {"any": {}},
}


def _llm_structured(prompt):
    """Call Bedrock with tool use, return structured dict or None."""
    try:
        bedrock = boto3.client(
            "bedrock-runtime", region_name=DEPLOY_REGION,
            config=Config(read_timeout=20, connect_timeout=5),
        )
        response = bedrock.converse(
            modelId=LLM_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            toolConfig=_SECURITY_TOOL,
        )
        for content in response["output"]["message"]["content"]:
            if "toolUse" in content:
                return content["toolUse"]["input"]
    except Exception as e:
        logger.warning("LLM analysis failed: %s", e)
    return None


def analyze_iam_risk(event_name, detail):
    """Assess risk of an IAM change. Returns dict with risk/should_alert/analysis or None."""
    request_params = detail.get("requestParameters") or {}
    policy_doc = request_params.get("policyDocument", "")
    role_name = request_params.get("roleName", "")
    policy_name = request_params.get("policyName", "")
    policy_arn = request_params.get("policyArn", "")

    policy_str = json.dumps(policy_doc) if isinstance(policy_doc, dict) else str(policy_doc)

    prompt = f"""You are an AWS security analyst. Assess the risk of this IAM change.

Action: {event_name}
Role: {role_name}
Policy: {policy_name or policy_arn}
Policy document (if available): {policy_str}

Consider: overly broad permissions (*, admin), cross-account trust, privilege escalation paths, sensitive service access (IAM, S3, KMS, SecretsManager).

Set should_alert=false for routine CI/CD operations (e.g. Terraform updating its own roles with scoped policies, attaching standard AWS managed policies, or normal service role creation). Only set should_alert=true if there is a genuine security concern worth human attention."""
    return _llm_structured(prompt)


def analyze_sg_change(event_name, detail):
    """Assess risk of a SG change. Returns dict with risk/should_alert/analysis or None."""
    request = detail.get("requestParameters") or {}
    group_id = request.get("groupId", "unknown")
    ip_permissions = request.get("ipPermissions", {}).get("items", [])

    rules_desc = []
    for perm in ip_permissions[:5]:
        proto = perm.get("ipProtocol", "")
        from_port = perm.get("fromPort", "")
        to_port = perm.get("toPort", "")
        cidrs = [r.get("cidrIp", "") for r in perm.get("ipRanges", {}).get("items", [])]
        rules_desc.append(f"{proto} {from_port}-{to_port} from {', '.join(cidrs) or 'various'}")

    prompt = f"""You are an AWS security analyst. Assess the risk of this security group change.

Action: {event_name}
Security Group: {group_id}
Rules: {'; '.join(rules_desc) or 'no rule details available'}

Consider: 0.0.0.0/0 exposure, sensitive ports (22, 3389, 3306, 5432, 6379, 27017), overly broad protocols (-1 = all traffic).

Set should_alert=false for routine changes (e.g. adding rules scoped to private CIDRs or other security groups). Only set should_alert=true if there is genuine exposure risk."""
    return _llm_structured(prompt)


def _is_service_linked_role(detail):
    """Check if the event was performed by an AWS service-linked role.

    Service-linked roles (AWSServiceRoleFor*) are managed by AWS services
    and perform automated internal operations (e.g. ECS ENI provisioning,
    RDS networking). These are not human or CI actions and should be
    silently suppressed.
    """
    identity = detail.get("userIdentity", {})
    if identity.get("type") != "AssumedRole":
        return False
    arn = identity.get("arn", "")
    parts = arn.split("/")
    role_name = parts[1] if len(parts) > 1 else ""
    return role_name.startswith("AWSServiceRoleFor")


def is_duplicate(action, resource):
    key = f"{action}:{resource}"
    resource_key = f"__resource__:{resource}"
    now = time.time()
    for k in list(_recent_alerts):
        if now - _recent_alerts[k] > DEDUP_WINDOW:
            del _recent_alerts[k]
    if key in _recent_alerts:
        return True
    # For deletions: suppress if any delete on same resource happened recently
    if action.startswith(("Delete", "Terminate")) and resource_key in _recent_alerts:
        return True
    _recent_alerts[key] = now
    if action.startswith(("Delete", "Terminate")):
        _recent_alerts[resource_key] = now
    return False


def _get_webhook(param_name):
    return get_webhook_url(ssm, param_name)


# ---------------------------------------------------------------------------
# AWS service display name from eventSource
# ---------------------------------------------------------------------------
def service_display_name(event_source):
    """Extract human-readable service name from eventSource.

    Short prefixes (<=4 chars) are AWS acronyms -> uppercase (ec2->EC2, s3->S3).
    Longer prefixes get capitalized (lambda->Lambda, bedrock->Bedrock).
    """
    if not event_source:
        return None
    prefix = event_source.split(".")[0]
    return prefix.upper() if len(prefix) <= 4 else prefix.capitalize()


# ---------------------------------------------------------------------------
# Identity / resource extraction — GitHub OIDC attribution
# ---------------------------------------------------------------------------
def parse_identity(detail):
    """Extract human-readable identity from CloudTrail userIdentity.

    Returns (actor_string, is_ci, ci_context).
    ci_context is a dict with GitHub OIDC session tags when the caller is
    a GitHub Actions OIDC-assumed role, else None.

    GitHub OIDC session tags (set by configure-aws-credentials action):
    - GitHubActor: the GitHub username that triggered the workflow
    - GitHubSHA: the commit SHA
    - GitHubRepo: org/repo (e.g. javaBin/moresleep)
    - GitHubRunID: the workflow run ID
    The OIDC sub claim format: repo:javaBin/{name}:ref:refs/heads/{branch}
    """
    identity = detail.get("userIdentity", {})
    arn = identity.get("arn", "unknown")
    identity_type = identity.get("type", "")

    if identity_type == "AssumedRole":
        parts = arn.split("/")
        role_name = parts[1] if len(parts) > 1 else "unknown"
        session = parts[2] if len(parts) > 2 else ""

        # GitHub OIDC: session tags are in principalTags
        principal_tags = identity.get("sessionContext", {}).get(
            "sessionIssuer", {}
        ).get("principalTags", {})

        # Also check environment/webIdentityDetails for OIDC sub claim
        web_identity = identity.get("sessionContext", {}).get(
            "webIdFederationData", {}
        )

        # Check if this is a GitHub Actions OIDC session
        # The role name will contain our CI role prefix
        is_github_ci = "javabin-ci-" in role_name.lower()

        if is_github_ci:
            ci_ctx = {}
            # Extract from OIDC sub claim if available
            sub_claim = web_identity.get("attributes", {}).get(
                "sub", session
            )
            # Parse sub: repo:javaBin/{repo}:ref:refs/heads/{branch}
            sub_match = re.match(
                r"repo:([^:]+):ref:refs/(?:heads|tags)/(.+)", sub_claim
            )
            if sub_match:
                ci_ctx["repo"] = sub_match.group(1)
                ci_ctx["ref"] = sub_match.group(2)

            # Session tags from OIDC (if present in CloudTrail)
            if principal_tags:
                for tag_key in ("GitHubActor", "GitHubSHA", "GitHubRepo", "GitHubRunID"):
                    val = principal_tags.get(tag_key)
                    if val:
                        ci_ctx[tag_key] = val[0] if isinstance(val, list) else val

            # Fallback: parse repo from session name
            # configure-aws-credentials uses the sub claim as session name
            if not ci_ctx.get("repo") and "repo:" in session:
                sub_match = re.match(
                    r"repo:([^:]+):ref:refs/(?:heads|tags)/(.+)", session
                )
                if sub_match:
                    ci_ctx["repo"] = sub_match.group(1)
                    ci_ctx["ref"] = sub_match.group(2)

            # Parse enriched session name: {actor}-{sha8}-{run_id}
            # The actor may contain hyphens, sha8 is 8 hex chars, run_id is digits
            enriched_match = re.match(
                r"^(.+)-([0-9a-f]{8})-(\d+)$", session
            )
            if enriched_match:
                if not ci_ctx.get("GitHubActor"):
                    ci_ctx["GitHubActor"] = enriched_match.group(1)
                if not ci_ctx.get("GitHubSHA"):
                    ci_ctx["GitHubSHA"] = enriched_match.group(2)
                if not ci_ctx.get("GitHubRunID"):
                    ci_ctx["GitHubRunID"] = enriched_match.group(3)
            else:
                # Legacy fallback: javabin-{purpose}-{run_id}
                if not ci_ctx.get("GitHubRunID"):
                    run_match = re.search(r"-(\d{8,})$", session)
                    if run_match:
                        ci_ctx["GitHubRunID"] = run_match.group(1)

            # Fallback: infer repo from role name if not resolved
            # javabin-ci-infra → javaBin/platform
            # javabin-ci-app-{repo} → javaBin/{repo}
            if not ci_ctx.get("repo") and not ci_ctx.get("GitHubRepo"):
                if "ci-infra" in role_name:
                    ci_ctx["repo"] = f"{GITHUB_ORG_URL.split('/')[-1]}/platform"
                else:
                    app_match = re.match(r"javabin-ci-app-(.+)", role_name)
                    if app_match:
                        ci_ctx["repo"] = f"{GITHUB_ORG_URL.split('/')[-1]}/{app_match.group(1)}"

            gh_actor = ci_ctx.get("GitHubActor", "")
            if gh_actor:
                return f"{gh_actor} via {role_name} (CI/CD)", True, ci_ctx or None
            return f"{role_name} (CI/CD)", True, ci_ctx or None

        # Identity Center SSO: AWSReservedSSO_{permission-set}_{hash}
        sso_match = re.match(r"AWSReservedSSO_(.+)_[0-9a-f]+$", role_name)
        if sso_match:
            display = sso_match.group(1)
            if session:
                return f"{display} ({session})", False, None
            return display, False, None

        # Non-CI assumed role
        if session:
            return f"{role_name} ({session})", False, None
        return role_name, False, None

    elif identity_type == "IAMUser":
        return identity.get("userName", arn.split("/")[-1]), False, None
    elif identity_type == "Root":
        return "Root account", False, None

    return arn, False, None


def format_ci_source(ci_context):
    """Build a Slack mrkdwn source line with GitHub links from CI context."""
    if not ci_context:
        return ":robot_face: *Source:* CI/CD pipeline"

    parts = []
    repo = ci_context.get("GitHubRepo") or ci_context.get("repo")
    sha = ci_context.get("GitHubSHA")
    run_id = ci_context.get("GitHubRunID")

    if repo:
        repo_url = f"https://github.com/{repo}"
        parts.append(f"<{repo_url}|{repo}>")
        if sha:
            parts.append(f"<{repo_url}/commit/{sha}|{sha[:8]}>")
        if run_id:
            parts.append(f"<{repo_url}/actions/runs/{run_id}|run>")

    if not parts:
        return ":robot_face: *Source:* CI/CD pipeline"

    source = f":robot_face: *Source:* {' / '.join(parts)}"
    actor = ci_context.get("GitHubActor")
    if actor:
        source += f" — by {actor}"
    return source


def extract_resource_name(event_name, detail):
    """Try to extract a human-readable resource name from CloudTrail event."""
    response = detail.get("responseElements") or {}
    request = detail.get("requestParameters") or {}

    # Event-specific extractors (before generic field loop)
    if event_name == "CreateTags":
        resources = request.get("resourcesSet", {}).get("items", [])
        if resources:
            return resources[0].get("resourceId")

    if event_name == "UpdateService":
        svc = request.get("service", "")
        if svc:
            return svc.split("/")[-1] if "/" in svc else svc

    if event_name == "ModifyTargetGroupAttributes":
        tg_arn = request.get("targetGroupArn", "")
        if "targetgroup/" in tg_arn:
            return tg_arn.split("targetgroup/")[1].split("/")[0]

    name_fields = [
        "bucketName", "functionName", "clusterName", "serviceName",
        "dBInstanceIdentifier", "tableName", "loadBalancerName",
        "natGatewayId", "groupId", "groupName", "roleName",
        "userName", "policyName", "topicArn", "queueUrl",
        "secretName", "stackName", "loadBalancerArn",
    ]

    for field in name_fields:
        val = response.get(field) or request.get(field)
        if val and isinstance(val, str):
            return val

    if event_name == "RunInstances":
        instances = response.get("instancesSet", {}).get("items", [])
        if instances:
            return instances[0].get("instanceId", "")

    if event_name in ("StopInstances", "TerminateInstances"):
        items = request.get("instancesSet", {}).get("items", [])
        if items:
            return items[0].get("instanceId", "")

    if event_name == "ModifyInstanceAttribute":
        return request.get("instanceId")

    # ELB: target groups, rules, listeners (nested response structures)
    for list_key in ("targetGroups", "rules", "listeners"):
        items = response.get(list_key, [])
        if items and isinstance(items, list):
            item = items[0]
            name = item.get("targetGroupName") or item.get("listenerArn") or item.get("ruleArn")
            if name:
                return name.split("/")[-1] if "/" in str(name) else name

    # Fallback: check request for name/arn patterns
    for key in ("name", "targetGroupArn", "listenerArn", "ruleArn"):
        val = request.get(key)
        if val and isinstance(val, str):
            return val.split("/")[-1] if "/" in val else val

    return None


# ---------------------------------------------------------------------------
# Console URL builder
# ---------------------------------------------------------------------------
def build_console_url(event_name, event_source, resource_name, region):
    """Build a console URL from eventSource."""
    if not resource_name or not region:
        return None

    service = event_source.split(".")[0] if event_source else ""
    if not service:
        return None

    if service in _CONSOLE_OVERRIDES:
        return _CONSOLE_OVERRIDES[service](resource_name, region)

    return f"https://{region}.console.aws.amazon.com/{service}/home?region={region}#/{resource_name}"


def resource_link(resource_name, url):
    """Wrap resource name in a Slack mrkdwn link if URL is available."""
    if url:
        return f"<{url}|{resource_name}>"
    return f"`{resource_name}`"


# ---------------------------------------------------------------------------
# Block Kit formatters
# ---------------------------------------------------------------------------

def _extract_tags(detail):
    """Extract resource tags from CloudTrail event request/response if available."""
    tags = {}
    for key in ("requestParameters", "responseElements"):
        container = detail.get(key) or {}
        # S3, Lambda, etc. use tagSet or tags
        for tag_field in ("tagSet", "tags", "tagList"):
            tag_data = container.get(tag_field)
            if isinstance(tag_data, dict):
                items = tag_data.get("items", tag_data.get("member", []))
            elif isinstance(tag_data, list):
                items = tag_data
            else:
                continue
            for item in items:
                if isinstance(item, dict):
                    k = item.get("key") or item.get("Key") or item.get("tagKey", "")
                    v = item.get("value") or item.get("Value") or item.get("tagValue", "")
                    if k:
                        tags[k] = v
    return tags


def _build_footer(parsed, tags=None, actor=None):
    """Build context footer with account, region, timestamp, and optionally tags + actor."""
    account = parsed.get("account", "unknown")
    region = parsed.get("region", "unknown")
    ts = parsed.get("time", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))

    footer_parts = [f"AWS Account {account} | {region} | {ts}"]

    if actor:
        footer_parts.append(f"By: {actor}")

    if tags:
        # Show key tags: team, service, created-by, commit
        display_tags = {k: v for k, v in tags.items()
                        if k in ("team", "service", "created-by", "commit", "environment", "repo")}
        if display_tags:
            tag_str = " | ".join(f"{k}={v}" for k, v in sorted(display_tags.items()))
            footer_parts.append(tag_str)

    return {
        "type": "context",
        "elements": [
            {"type": "mrkdwn", "text": "\n".join(footer_parts)}
        ]
    }


def format_resource_creation(parsed):
    """Resource creation alert — Block Kit."""
    detail = parsed.get("detail", {})
    event_name = detail.get("eventName", "unknown")
    event_source = detail.get("eventSource", "")
    actor, is_ci, ci_ctx = parse_identity(detail)
    resource_name = extract_resource_name(event_name, detail)
    region = parsed.get("region", "unknown")

    if is_duplicate(event_name, resource_name or "unknown"):
        return None

    display_name = re.sub(r"\d{8}$", "", event_name)
    console_url = build_console_url(event_name, event_source, resource_name, region)
    res_display = resource_link(resource_name, console_url) if resource_name else "_unknown_"
    service = service_display_name(event_source)
    tags = _extract_tags(detail)
    cost_estimate = estimate_cost(event_name, detail)

    if is_ci:
        _track_ci_resource(resource_name)
        header_text = f":package: {service or 'Resource'} Created (CI/CD)"
        source_line = format_ci_source(ci_ctx)
    else:
        header_text = f":warning: {service or 'Resource'} Created (Console)"
        source_line = ":bust_in_silhouette: *Source:* Manual console action"

    fields = [
        {"type": "mrkdwn", "text": f"*Resource*\n{res_display}"},
    ]
    if cost_estimate:
        fields.append({"type": "mrkdwn", "text": f"*Est. Cost*\n{cost_estimate}"})
    fields.append({"type": "mrkdwn", "text": f"*Action*\n{display_name}"})
    fields.append({"type": "mrkdwn", "text": f"*Region*\n{region}"})

    blocks = [
        {"type": "header", "text": {"type": "plain_text", "text": header_text, "emoji": True}},
        {"type": "section", "text": {"type": "mrkdwn", "text": source_line}},
        {"type": "section", "fields": fields},
    ]

    # LLM gates SG change alerts
    if event_name in ("AuthorizeSecurityGroupIngress", "AuthorizeSecurityGroupEgress"):
        sg_result = analyze_sg_change(event_name, detail)
        if sg_result and not sg_result.get("should_alert"):
            logger.info("SG change suppressed by LLM (risk=%s): %s on %s",
                        sg_result.get("risk"), event_name, resource_name)
            return None
        if sg_result:
            re_emoji = risk_emoji(sg_result.get("risk"))
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": f":brain: *Security Analysis* {re_emoji} {sg_result.get('risk', '')}\n{sg_result.get('analysis', '')}"}
            })

    if not is_ci:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "_Review this resource to ensure it has proper tags and is expected._"}
        })

    blocks.append({"type": "divider"})
    blocks.append(_build_footer(parsed, tags=tags, actor=actor))

    fallback = f"{'CI/CD' if is_ci else 'Console'} created {service or display_name}: {resource_name or 'unknown'}"
    return {"blocks": blocks, "text": fallback}


def extract_changes(event_name, detail):
    """Extract old/new values from CloudTrail where available."""
    request = detail.get("requestParameters") or {}
    changes = []

    if event_name == "ModifyDBInstance":
        for key in ("dBInstanceClass", "allocatedStorage", "engineVersion",
                     "multiAZ", "storageType"):
            new_val = request.get(key)
            if new_val is not None:
                changes.append((key, str(new_val)))

    elif event_name == "ModifyInstanceAttribute":
        attr = request.get("attribute", "")
        val = request.get("value", request.get("instanceType", {}).get("value"))
        if attr and val:
            changes.append((attr, str(val)))

    elif event_name == "UpdateService":
        for key in ("desiredCount", "taskDefinition"):
            new_val = request.get(key)
            if new_val is not None:
                label = key
                val = str(new_val)
                if key == "taskDefinition" and ":" in val:
                    val = val.rsplit("/", 1)[-1]
                changes.append((label, val))

    elif event_name == "UpdateFunctionConfiguration":
        for key in ("memorySize", "timeout", "runtime", "handler"):
            new_val = request.get(key)
            if new_val is not None:
                changes.append((key, str(new_val)))

    elif event_name in ("RevokeSecurityGroupIngress", "RevokeSecurityGroupEgress"):
        items = request.get("ipPermissions", {}).get("items", [])
        for item in items[:3]:
            proto = item.get("ipProtocol", "")
            from_port = item.get("fromPort", "")
            to_port = item.get("toPort", "")
            ranges = [r.get("cidrIp", "") for r in item.get("ipRanges", {}).get("items", [])]
            desc = f"{proto} {from_port}-{to_port}"
            if ranges:
                desc += f" from {', '.join(ranges)}"
            changes.append(("revoked rule", desc))

    return changes


def format_resource_modification(parsed):
    """Resource modification alert — Block Kit."""
    detail = parsed.get("detail", {})
    event_name = detail.get("eventName", "unknown")
    event_source = detail.get("eventSource", "")
    actor, is_ci, ci_ctx = parse_identity(detail)
    resource_name = extract_resource_name(event_name, detail)
    region = parsed.get("region", "unknown")

    if is_duplicate(event_name, resource_name or "unknown"):
        return None

    if is_ci:
        _track_ci_resource(resource_name)

    display_name = re.sub(r"\d{8}$", "", event_name)
    console_url = build_console_url(event_name, event_source, resource_name, region)
    res_display = resource_link(resource_name, console_url) if resource_name else "_unknown_"
    service = service_display_name(event_source)
    tags = _extract_tags(detail)

    fields = [
        {"type": "mrkdwn", "text": f"*Resource*\n{res_display}"},
        {"type": "mrkdwn", "text": f"*Action*\n{display_name}"},
        {"type": "mrkdwn", "text": f"*Region*\n{region}"},
    ]

    source_line = format_ci_source(ci_ctx) if is_ci else None

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": f":wrench: {service or 'Resource'} Modified", "emoji": True}},
    ]
    if source_line:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": source_line}})
    blocks.append({"type": "section", "fields": fields})

    changes = extract_changes(event_name, detail)
    if changes:
        change_lines = "\n".join(f"{k}: {v}" for k, v in changes)
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f"*Changes*\n```{change_lines}```"}
        })

    # LLM gates SG modification alerts
    if event_name in ("ModifySecurityGroupRules",
                      "RevokeSecurityGroupIngress", "RevokeSecurityGroupEgress"):
        sg_result = analyze_sg_change(event_name, detail)
        if sg_result and not sg_result.get("should_alert"):
            logger.info("SG modification suppressed by LLM (risk=%s): %s on %s",
                        sg_result.get("risk"), event_name, resource_name)
            return None
        if sg_result:
            re_emoji = risk_emoji(sg_result.get("risk"))
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": f":brain: *Security Analysis* {re_emoji} {sg_result.get('risk', '')}\n{sg_result.get('analysis', '')}"}
            })

    blocks.append({"type": "divider"})
    blocks.append(_build_footer(parsed, tags=tags, actor=actor))

    fallback = f"{service or 'Resource'} modified: {display_name} on {resource_name or 'unknown'}"
    return {"blocks": blocks, "text": fallback}


def format_resource_deletion(parsed):
    """Resource deletion alert — Block Kit."""
    detail = parsed.get("detail", {})
    event_name = detail.get("eventName", "unknown")
    event_source = detail.get("eventSource", "")
    actor, is_ci, ci_ctx = parse_identity(detail)
    resource_name = extract_resource_name(event_name, detail)
    region = parsed.get("region", "unknown")

    if is_duplicate(event_name, resource_name or "unknown"):
        return None

    # Track CI-managed resources to suppress noisy SecurityHub/GuardDuty findings
    if is_ci:
        _track_ci_resource(resource_name)

    display_name = re.sub(r"\d{8}$", "", event_name)
    service = service_display_name(event_source)
    tags = _extract_tags(detail)

    fields = [
        {"type": "mrkdwn", "text": f"*Resource*\n`{resource_name or 'unknown'}`"},
        {"type": "mrkdwn", "text": f"*Action*\n{display_name}"},
        {"type": "mrkdwn", "text": f"*Region*\n{region}"},
    ]

    source_line = format_ci_source(ci_ctx) if is_ci else None

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": f":wastebasket: {service or 'Resource'} Deleted", "emoji": True}},
    ]
    if source_line:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": source_line}})
    blocks.extend([
        {"type": "section", "fields": fields},
        {"type": "divider"},
        _build_footer(parsed, tags=tags, actor=actor),
    ])

    fallback = f"{service or 'Resource'} deleted: {resource_name or 'unknown'}"
    return {"blocks": blocks, "text": fallback}


def format_compliance_alert(parsed):
    """Untagged resource alert — Block Kit. Returns None for non-project resources."""
    detail = parsed.get("detail", {})
    resource_id = detail.get("resourceId", "unknown")
    resource_type = detail.get("resourceType", "unknown")

    if PROJECT_PREFIX not in resource_id.lower():
        return None

    # DynamoDB dedup for compliance alerts
    rule_name = detail.get("configRuleName", "unknown")
    dedup_key = f"compliance:{resource_id}:{rule_name}"
    if is_finding_already_alerted(dedup_key):
        logger.info("Compliance alert suppressed (already alerted): %s", dedup_key)
        return None
    record_finding_alert(dedup_key)

    compliance = detail.get("newEvaluationResult", {}).get("complianceType", "UNKNOWN")
    short_type = resource_type.replace("AWS::", "").replace("::", " ")

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": ":red_circle: Untagged Resource Detected", "emoji": True}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": "A resource is missing required tags (`service`, `team`, `managed-by`)."}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Resource Type*\n{short_type}"},
            {"type": "mrkdwn", "text": f"*Resource ID*\n`{resource_id}`"},
            {"type": "mrkdwn", "text": f"*Config Rule*\n{rule_name}"},
            {"type": "mrkdwn", "text": f"*Compliance*\n{compliance}"},
        ]},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": "_Tags should be applied via CI/CD. Check the deploying workflow._"}},
        {"type": "divider"},
        _build_footer(parsed),
    ]

    fallback = f"Untagged resource: {short_type} — {resource_id}"
    return {"blocks": blocks, "text": fallback}


def format_iam_change(parsed):
    """IAM change alert — Block Kit. LLM decides if it's worth alerting."""
    detail = parsed.get("detail", {})
    event_name = detail.get("eventName", "unknown")
    actor, is_ci, ci_ctx = parse_identity(detail)
    region = parsed.get("region", "unknown")

    request_params = detail.get("requestParameters") or {}
    role_name = request_params.get("roleName", "")
    policy_name = request_params.get("policyName", "")
    user_name = request_params.get("userName", "")
    target = role_name or policy_name or user_name or "unknown"

    # LLM decides whether this IAM change deserves a Slack alert
    risk_result = analyze_iam_risk(event_name, detail)
    if risk_result and not risk_result.get("should_alert"):
        logger.info("IAM change suppressed by LLM (risk=%s): %s on %s",
                     risk_result.get("risk"), event_name, target)
        return None

    console_url = build_console_url(event_name, "iam.amazonaws.com", target, region)
    target_display = resource_link(target, console_url)

    source_line = format_ci_source(ci_ctx) if is_ci else None

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": ":shield: IAM Change Detected", "emoji": True}},
    ]
    if source_line:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": source_line}})
    blocks.append({"type": "section", "fields": [
        {"type": "mrkdwn", "text": f"*Action*\n{event_name}"},
        {"type": "mrkdwn", "text": f"*Target*\n{target_display}"},
        {"type": "mrkdwn", "text": f"*Performed by*\n{actor}"},
        {"type": "mrkdwn", "text": f"*Region*\n{region}"},
    ]})

    if risk_result:
        re_emoji = risk_emoji(risk_result.get("risk"))
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f":brain: *Risk Assessment* {re_emoji} {risk_result.get('risk', '')}\n{risk_result.get('analysis', '')}"}
        })

    blocks.append({"type": "divider"})
    blocks.append(_build_footer(parsed, actor=actor))

    fallback = f"IAM change: {event_name} on {target} by {actor}"
    return {"blocks": blocks, "text": fallback}


def format_console_login(parsed):
    """Console login alert — Block Kit."""
    detail = parsed.get("detail", {})
    actor, _, _ = parse_identity(detail)
    result = (detail.get("responseElements") or {}).get("ConsoleLogin", "Unknown")
    region = parsed.get("region", "unknown")

    result_emoji = ":white_check_mark:" if result == "Success" else ":x:"

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": ":key: Console Login", "emoji": True}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*User*\n{actor}"},
            {"type": "mrkdwn", "text": f"*Result*\n{result_emoji} {result}"},
            {"type": "mrkdwn", "text": f"*Region*\n{region}"},
        ]},
        {"type": "divider"},
        _build_footer(parsed, actor=actor),
    ]

    fallback = f"Console login: {actor} — {result}"
    return {"blocks": blocks, "text": fallback}


def format_guardduty_finding(parsed):
    """GuardDuty finding alert — Block Kit."""
    detail = parsed.get("detail", {})
    finding_type = detail.get("type", "unknown")
    severity = detail.get("severity", 0)
    title = detail.get("title", "GuardDuty Finding")
    description = detail.get("description", "")
    region = parsed.get("region", detail.get("region", "unknown"))
    account = parsed.get("account", detail.get("accountId", "unknown"))

    # Dedup root credential usage — GuardDuty fires per API call (ConsoleLogin,
    # Search, GetIdentityMetadata, etc.).  One alert per hour is enough.
    if finding_type == "Policy:IAMUser/RootCredentialUsage":
        hour_key = datetime.now(timezone.utc).strftime("%Y-%m-%d-%H")
        dedup_key = f"guardduty:root:{hour_key}"
        if is_finding_already_alerted(dedup_key):
            logger.info("GuardDuty root credential finding suppressed (hourly dedup): %s", title)
            return None
        record_finding_alert(dedup_key)

    # Suppress findings for resources recently managed by CI
    resource = detail.get("resource", {})
    for s3_detail in resource.get("S3BucketDetails", []):
        bucket_name = s3_detail.get("Name", "")
        if _is_ci_managed_resource(bucket_name):
            logger.info("GuardDuty finding suppressed (CI-managed resource): %s on %s",
                        finding_type, bucket_name)
            return None

    if severity >= 7:
        sev_emoji = ":red_circle:"
        sev_label = "HIGH"
    elif severity >= 4:
        sev_emoji = ":large_orange_circle:"
        sev_label = "MEDIUM"
    else:
        sev_emoji = ":yellow_circle:"
        sev_label = "LOW"

    resource = detail.get("resource", {})
    resource_type = resource.get("resourceType", "Unknown")
    resource_id = "unknown"
    if resource_type == "Instance":
        instance = resource.get("instanceDetails", {})
        resource_id = instance.get("instanceId", "unknown")
    elif resource_type == "ECSCluster":
        ecs_details = resource.get("ecsClusterDetails", {})
        resource_id = ecs_details.get("arn", "unknown").split("/")[-1]
    elif resource_type == "Container":
        container = resource.get("containerDetails", {})
        resource_id = container.get("name", "unknown")
    elif resource_type == "AccessKey":
        access_key = resource.get("accessKeyDetails", {})
        resource_id = access_key.get("userName", access_key.get("principalId", "unknown"))

    gd_url = f"https://{region}.console.aws.amazon.com/guardduty/home?region={region}#/findings"

    fields = [
        {"type": "mrkdwn", "text": f"*Finding Type*\n`{finding_type}`"},
        {"type": "mrkdwn", "text": f"*Severity*\n{sev_emoji} {sev_label} ({severity})"},
        {"type": "mrkdwn", "text": f"*Resource*\n{resource_type}: `{resource_id}`"},
        {"type": "mrkdwn", "text": f"*Region*\n{region}"},
    ]

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": f"{sev_emoji} GuardDuty: {title[:140]}", "emoji": True}},
        {"type": "section", "fields": fields},
    ]

    if description:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"_{description[:500]}_"}
        })

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"<{gd_url}|View in GuardDuty Console>"}
    })
    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"AWS Account {account} | {region} | Source: GuardDuty"}]
    })

    fallback = f"GuardDuty [{sev_label}]: {title}"
    return {"blocks": blocks, "text": fallback}


def format_securityhub_finding(parsed):
    """Security Hub finding alert — Block Kit with DynamoDB dedup and Acknowledge button."""
    findings = parsed.get("detail", {}).get("findings", [])
    if not findings:
        return None

    finding = findings[0]
    title = finding.get("Title", "Security Hub Finding")
    severity_label = finding.get("Severity", {}).get("Label", "INFORMATIONAL")
    description = finding.get("Description", "")
    region = parsed.get("region", "unknown")
    account = parsed.get("account", "unknown")
    finding_id = finding.get("Id", "")

    # Suppress findings for resources recently managed by CI
    for res in finding.get("Resources", []):
        res_id = res.get("Id", "")
        # Extract resource name from ARN (e.g. arn:aws:s3:::aws.javabin.no -> aws.javabin.no)
        resource_name = res_id.split(":")[-1].split("/")[-1] if res_id else ""
        if _is_ci_managed_resource(resource_name):
            logger.info("Security Hub finding suppressed (CI-managed resource): %s on %s",
                        title, resource_name)
            return None

    # DynamoDB dedup — suppress if already alerted for this resource+finding
    dedup_key = _finding_dedup_key(finding)
    if is_finding_already_alerted(dedup_key):
        logger.info("Security Hub finding suppressed (already alerted): %s", dedup_key)
        return None
    record_finding_alert(dedup_key)

    sev_emoji = {
        "CRITICAL": ":red_circle:",
        "HIGH": ":red_circle:",
        "MEDIUM": ":large_orange_circle:",
        "LOW": ":yellow_circle:",
    }.get(severity_label, ":white_circle:")

    resources = finding.get("Resources", [])
    resource_ids = [r.get("Id", "unknown").split("/")[-1] for r in resources[:3]]

    fields = [
        {"type": "mrkdwn", "text": f"*Severity*\n{sev_emoji} {severity_label}"},
        {"type": "mrkdwn", "text": f"*Resources*\n{', '.join(resource_ids)}"},
        {"type": "mrkdwn", "text": f"*Region*\n{region}"},
    ]

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": f"{sev_emoji} Security Hub: {title[:140]}", "emoji": True}},
        {"type": "section", "fields": fields},
    ]

    if description:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f"_{description[:500]}_"}
        })

    # Link to view & suppress in Security Hub console
    encoded_id = finding_id.replace("/", "%2F").replace(":", "%3A")
    sh_finding_url = (
        f"https://{region}.console.aws.amazon.com/securityhub/home"
        f"?region={region}#/findings?search=Id%3D{encoded_id}"
    )
    blocks.append({
        "type": "actions",
        "elements": [{
            "type": "button",
            "text": {"type": "plain_text", "text": "View in Security Hub", "emoji": True},
            "url": sh_finding_url,
        }],
    })

    blocks.append({"type": "divider"})
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"AWS Account {account} | {region} | Source: Security Hub"}]
    })

    fallback = f"Security Hub [{severity_label}]: {title}"
    return {"blocks": blocks, "text": fallback}


def format_cost_anomaly(parsed):
    """Cost anomaly alert — Block Kit."""
    anomaly_id = parsed.get("anomalyId", "unknown")
    root_causes = parsed.get("rootCauses", [])

    fields = [{"type": "mrkdwn", "text": f"*Anomaly ID*\n`{anomaly_id}`"}]

    if root_causes:
        cause = root_causes[0]
        service = cause.get("service", "unknown")
        region = cause.get("region", "unknown")
        fields.append({"type": "mrkdwn", "text": f"*Service*\n{service}"})
        fields.append({"type": "mrkdwn", "text": f"*Region*\n{region}"})

    anomaly_detail = parsed.get("anomalyDetails", parsed.get("anomaly", {}))
    if isinstance(anomaly_detail, dict):
        expected = anomaly_detail.get("expectedSpend", {}).get("maxAmount")
        actual = anomaly_detail.get("actualSpend")
        severity = anomaly_detail.get("severity")
        if expected:
            fields.append({"type": "mrkdwn", "text": f"*Expected*\n${float(expected):.2f}"})
        if actual:
            fields.append({"type": "mrkdwn", "text": f"*Actual*\n${float(actual):.2f}"})
        if severity:
            fields.append({"type": "mrkdwn", "text": f"*Severity*\n{severity}"})

    ce_url = "https://us-east-1.console.aws.amazon.com/cost-management/home#/anomaly-detection"

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": ":chart_with_upwards_trend: Cost Anomaly Detected",
                  "emoji": True}},
        {"type": "section", "fields": fields},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": f"<{ce_url}|View in Cost Explorer>"}},
        {"type": "divider"},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": "Source: AWS Cost Anomaly Detection"}
        ]},
    ]

    fallback = f"Cost anomaly detected: {anomaly_id}"
    return {"blocks": blocks, "text": fallback}


def format_budget_alert(parsed):
    """Budget alert — Block Kit."""
    budget_name = parsed.get("budgetName", "unknown")
    threshold = parsed.get("threshold", "N/A")
    actual = parsed.get("actualAmount", "N/A")
    limit = parsed.get("budgetLimit", "N/A")

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": ":rotating_light: Budget Alert", "emoji": True}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": f"Budget *{budget_name}* has exceeded its threshold."}},
        {"type": "section", "fields": [
            {"type": "mrkdwn", "text": f"*Threshold*\n{threshold}%"},
            {"type": "mrkdwn", "text": f"*Current Spend*\n${actual}"},
            {"type": "mrkdwn", "text": f"*Budget Limit*\n${limit}"},
        ]},
        {"type": "divider"},
        {"type": "context", "elements": [
            {"type": "mrkdwn", "text": "Source: AWS Budgets"}
        ]},
    ]

    fallback = f"Budget alert: {budget_name} at {threshold}% (${actual} / ${limit})"
    return {"blocks": blocks, "text": fallback}


# ---------------------------------------------------------------------------
# Weekly Security Hub summary
# ---------------------------------------------------------------------------
def _extract_resource_type(finding):
    """Extract a short resource type from a Security Hub finding."""
    resources = finding.get("Resources", [])
    if not resources:
        return "Unknown"
    # Type looks like "AwsS3Bucket", "AwsEc2SecurityGroup", etc.
    raw = resources[0].get("Type", "Other")
    # Strip "Aws" prefix and insert spaces before capitals
    name = raw.removeprefix("Aws")
    return re.sub(r"(?<=[a-z])(?=[A-Z])", " ", name)


def _severity_cell(label):
    """Build a rich_text table cell with emoji for severity."""
    emoji_name = "red_circle" if label == "CRITICAL" else "large_orange_circle"
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


def format_securityhub_summary():
    """Query Security Hub for active HIGH/CRITICAL findings and format a table summary."""
    securityhub = boto3.client("securityhub", region_name=DEPLOY_REGION)

    findings = []
    next_token = None
    while True:
        kwargs = {
            "Filters": {
                "SeverityLabel": [
                    {"Value": "HIGH", "Comparison": "EQUALS"},
                    {"Value": "CRITICAL", "Comparison": "EQUALS"},
                ],
                "WorkflowStatus": [
                    {"Value": "NEW", "Comparison": "EQUALS"},
                    {"Value": "NOTIFIED", "Comparison": "EQUALS"},
                ],
                "RecordState": [
                    {"Value": "ACTIVE", "Comparison": "EQUALS"},
                ],
            },
            "MaxResults": 100,
        }
        if next_token:
            kwargs["NextToken"] = next_token
        resp = securityhub.get_findings(**kwargs)
        findings.extend(resp.get("Findings", []))
        next_token = resp.get("NextToken")
        if not next_token:
            break

    if not findings:
        return None

    # Sort: CRITICAL first, then HIGH, then by resource
    sev_order = {"CRITICAL": 0, "HIGH": 1}
    findings.sort(key=lambda f: (
        sev_order.get(f.get("Severity", {}).get("Label", "HIGH"), 1),
        f.get("Resources", [{}])[0].get("Id", "") if f.get("Resources") else "",
    ))

    # Build table rows — header + one row per finding
    header_row = [
        {"type": "raw_text", "text": "Severity"},
        {"type": "raw_text", "text": "Type"},
        {"type": "raw_text", "text": "Resource"},
        {"type": "raw_text", "text": "Finding"},
    ]

    rows = [header_row]
    for f in findings[:50]:  # Cap at 50 rows to stay within Block Kit limits
        sev_label = f.get("Severity", {}).get("Label", "HIGH")
        resource_type = _extract_resource_type(f)
        resources = f.get("Resources", [])
        resource_id = resources[0].get("Id", "unknown").split("/")[-1] if resources else "unknown"
        title = f.get("Title", "Unknown")

        rows.append([
            _severity_cell(sev_label),
            {"type": "raw_text", "text": resource_type},
            {"type": "raw_text", "text": resource_id},
            {"type": "raw_text", "text": title[:120]},
        ])

    region = DEPLOY_REGION
    sh_url = f"https://{region}.console.aws.amazon.com/securityhub/home?region={region}#/findings"
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": "Weekly Security Hub Summary", "emoji": True}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": f"*{len(findings)} active HIGH/CRITICAL findings*"}},
        {
            "type": "table",
            "column_settings": [
                {"align": "center"},
                {"is_wrapped": True},
                {"is_wrapped": True},
                {"is_wrapped": True},
            ],
            "rows": rows,
        },
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": f"<{sh_url}|View all findings in Security Hub>"}},
        {"type": "divider"},
        {"type": "context",
         "elements": [{"type": "mrkdwn", "text": f"Weekly summary | {ts}"}]},
    ]

    return {
        "blocks": blocks,
        "text": f"Weekly Security Hub Summary: {len(findings)} active findings",
    }


def summary_handler(event, context):
    """Lambda handler for the weekly Security Hub summary."""
    webhook_url = _get_webhook(SECURITY_WEBHOOK_PARAM if SECURITY_WEBHOOK_PARAM else INFRA_WEBHOOK_PARAM)
    try:
        result = format_securityhub_summary()
        if result:
            post_to_slack(webhook_url, result)
            logger.info("Posted weekly Security Hub summary")
        else:
            logger.info("No active HIGH/CRITICAL findings — skipping summary")
    except Exception as e:
        logger.error("Weekly summary failed: %s", e)

    return {"statusCode": 200}


# ---------------------------------------------------------------------------
# Main handler
# ---------------------------------------------------------------------------
def _is_security_finding(parsed):
    """Check if the event is a Security Hub or GuardDuty finding."""
    detail_type = parsed.get("detail-type", "")
    return detail_type in (
        "Security Hub Findings - Imported",
        "GuardDuty Finding",
    )


def handler(event, context):
    for record in event["Records"]:
        sns_message = record["Sns"]
        topic_arn = sns_message.get("TopicArn", "")
        subject = sns_message.get("Subject", "AWS Alert")
        raw_message = sns_message["Message"]

        # Default webhook based on SNS topic
        if topic_arn == SECURITY_TOPIC_ARN:
            webhook_url = _get_webhook(INFRA_WEBHOOK_PARAM)
        else:
            webhook_url = _get_webhook(COST_WEBHOOK_PARAM)

        try:
            parsed = json.loads(raw_message)
            # Route Security Hub + GuardDuty findings to dedicated security channel
            if (topic_arn == SECURITY_TOPIC_ARN
                    and SECURITY_WEBHOOK_PARAM
                    and _is_security_finding(parsed)):
                webhook_url = _get_webhook(SECURITY_WEBHOOK_PARAM)
            result = format_structured_alert(subject, parsed)
        except (json.JSONDecodeError, TypeError):
            result = format_plain_alert(subject, raw_message)

        if result:
            logger.info("Posting alert: %s", result.get("text", subject))
            post_to_slack(webhook_url, result)
        else:
            logger.info("Alert filtered (dedup or non-project): %s", subject)


def format_structured_alert(subject, parsed):
    """Route to the correct Block Kit formatter."""
    # Budget alert
    if "budgetName" in parsed:
        return format_budget_alert(parsed)

    # GuardDuty finding
    if parsed.get("detail-type") == "GuardDuty Finding":
        return format_guardduty_finding(parsed)

    # Config compliance change
    if parsed.get("detail-type") == "Config Rules Compliance Change":
        return format_compliance_alert(parsed)

    # Security Hub finding
    if parsed.get("detail-type") == "Security Hub Findings - Imported":
        return format_securityhub_finding(parsed)

    # Cost Anomaly
    if "anomalyId" in parsed or "rootCauses" in parsed:
        return format_cost_anomaly(parsed)

    # EventBridge / CloudTrail events
    if "detail-type" in parsed:
        detail = parsed.get("detail", {})
        event_name = detail.get("eventName", "")

        # Suppress automated actions by AWS service-linked roles
        if _is_service_linked_role(detail):
            logger.info("Suppressed service-linked role event: %s", event_name)
            return None

        if event_name.startswith(("Delete", "Terminate", "Stop")):
            return format_resource_deletion(parsed)

        if event_name.startswith(("Modify", "Update", "Revoke")):
            return format_resource_modification(parsed)

        if event_name.startswith(("Create", "Run")) or event_name in (
            "PutBucketPolicy",
            "AuthorizeSecurityGroupIngress",
            "AuthorizeSecurityGroupEgress",
        ):
            return format_resource_creation(parsed)

        if parsed["detail-type"] == "AWS Console Sign In via CloudTrail":
            return format_console_login(parsed)

        return format_iam_change(parsed)

    # Unknown structured event
    blocks = [
        {"type": "section",
         "text": {"type": "mrkdwn", "text": f"*{subject}*\n```{json.dumps(parsed, indent=2)[:2900]}```"}},
    ]
    return {"blocks": blocks, "text": subject}


def format_plain_alert(subject, message):
    blocks = [
        {"type": "section",
         "text": {"type": "mrkdwn", "text": f"*{subject}*\n{message[:2900]}"}},
    ]
    return {"blocks": blocks, "text": subject}


