import json
import logging
import os
import re
import time
import urllib.request
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from shared.pricing import estimate_cost

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

# SSM parameter names passed via environment
INFRA_WEBHOOK_PARAM = os.environ["INFRA_WEBHOOK_PARAM"]
COST_WEBHOOK_PARAM = os.environ["COST_WEBHOOK_PARAM"]
SECURITY_TOPIC_ARN = os.environ["SECURITY_TOPIC_ARN"]
PROJECT_PREFIX = os.environ.get("PROJECT_PREFIX", "javabin")
GITHUB_ORG_URL = os.environ.get("GITHUB_ORG_URL", "https://github.com/javaBin")

# Cache webhook URLs across invocations
_webhook_cache = {}

# Dedup cache: "{action}:{resource}" -> timestamp
_recent_alerts = {}
DEDUP_WINDOW = 300  # 5 minutes

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
LLM_MODEL_ID = "eu.anthropic.claude-haiku-4-5-20251001-v1:0"

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
            inferenceConfig={"temperature": 0.2},
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


def is_duplicate(action, resource):
    key = f"{action}:{resource}"
    now = time.time()
    for k in list(_recent_alerts):
        if now - _recent_alerts[k] > DEDUP_WINDOW:
            del _recent_alerts[k]
    if key in _recent_alerts:
        return True
    _recent_alerts[key] = now
    return False


def get_webhook_url(param_name):
    if param_name not in _webhook_cache:
        resp = ssm.get_parameter(Name=param_name, WithDecryption=True)
        _webhook_cache[param_name] = resp["Parameter"]["Value"]
    return _webhook_cache[param_name]


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

            return f"{role_name} (CI/CD)", True, ci_ctx or None

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
    actor = ci_context.get("GitHubActor")

    if repo:
        repo_url = f"https://github.com/{repo}"
        parts.append(f"<{repo_url}|{repo}>")
        if sha:
            parts.append(f"<{repo_url}/commit/{sha}|{sha[:8]}>")
        if run_id:
            parts.append(f"<{repo_url}/actions/runs/{run_id}|run>")

    source_line = f":robot_face: *Source:* {' / '.join(parts)}" if parts else ":robot_face: *Source:* CI/CD pipeline"
    if actor:
        source_line += f" (triggered by {actor})"
    return source_line


def extract_resource_name(event_name, detail):
    """Try to extract a human-readable resource name from CloudTrail event."""
    response = detail.get("responseElements") or {}
    request = detail.get("requestParameters") or {}

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
def context_footer(parsed):
    """Standard context footer block."""
    account = parsed.get("account", "unknown")
    region = parsed.get("region", "unknown")
    ts = parsed.get("time", datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"))
    return {
        "type": "context",
        "elements": [
            {"type": "mrkdwn",
             "text": f"AWS Account {account} | {region} | {ts}"}
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

    if is_ci:
        header_text = ":package: Resource Created (CI/CD)"
        source_line = format_ci_source(ci_ctx)
    else:
        header_text = ":warning: Resource Created (Console)"
        source_line = ":bust_in_silhouette: *Source:* Manual console action"

    cost_estimate = estimate_cost(event_name, detail)

    fields = [
        {"type": "mrkdwn", "text": f"*Action*\n{display_name}"},
        {"type": "mrkdwn", "text": f"*Resource*\n{res_display}"},
        {"type": "mrkdwn", "text": f"*Created by*\n{actor}"},
        {"type": "mrkdwn", "text": f"*Region*\n{region}"},
    ]
    if service:
        fields.append({"type": "mrkdwn", "text": f"*Service*\n{service}"})
    if cost_estimate:
        fields.append({"type": "mrkdwn", "text": f"*Est. Cost*\n{cost_estimate}"})

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
            risk_emoji = {"HIGH": ":red_circle:", "MEDIUM": ":large_orange_circle:"}.get(
                sg_result.get("risk"), ":yellow_circle:")
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": f":brain: *Security Analysis* {risk_emoji} {sg_result.get('risk', '')}\n{sg_result.get('analysis', '')}"}
            })

    if not is_ci:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": "_Review this resource to ensure it has proper tags and is expected._"}
        })

    blocks.append({"type": "divider"})
    blocks.append(context_footer(parsed))

    fallback = f"{'CI/CD' if is_ci else 'Console'} created {display_name}: {resource_name or 'unknown'}"
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

    display_name = re.sub(r"\d{8}$", "", event_name)
    console_url = build_console_url(event_name, event_source, resource_name, region)
    res_display = resource_link(resource_name, console_url) if resource_name else "_unknown_"
    service = service_display_name(event_source)

    fields = [
        {"type": "mrkdwn", "text": f"*Action*\n{display_name}"},
        {"type": "mrkdwn", "text": f"*Resource*\n{res_display}"},
        {"type": "mrkdwn", "text": f"*Modified by*\n{actor}"},
        {"type": "mrkdwn", "text": f"*Region*\n{region}"},
    ]
    if service:
        fields.append({"type": "mrkdwn", "text": f"*Service*\n{service}"})

    source_line = format_ci_source(ci_ctx) if is_ci else None

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": ":wrench: Resource Modified", "emoji": True}},
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
            risk_emoji = {"HIGH": ":red_circle:", "MEDIUM": ":large_orange_circle:"}.get(
                sg_result.get("risk"), ":yellow_circle:")
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn",
                         "text": f":brain: *Security Analysis* {risk_emoji} {sg_result.get('risk', '')}\n{sg_result.get('analysis', '')}"}
            })

    blocks.append({"type": "divider"})
    blocks.append(context_footer(parsed))

    fallback = f"Resource modified: {display_name} on {resource_name or 'unknown'} by {actor}"
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

    display_name = re.sub(r"\d{8}$", "", event_name)
    service = service_display_name(event_source)

    fields = [
        {"type": "mrkdwn", "text": f"*Action*\n{display_name}"},
        {"type": "mrkdwn", "text": f"*Resource*\n`{resource_name or 'unknown'}`"},
        {"type": "mrkdwn", "text": f"*Deleted by*\n{actor}"},
        {"type": "mrkdwn", "text": f"*Region*\n{region}"},
    ]
    if service:
        fields.append({"type": "mrkdwn", "text": f"*Service*\n{service}"})

    source_line = format_ci_source(ci_ctx) if is_ci else None

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": ":wastebasket: Resource Deleted", "emoji": True}},
    ]
    if source_line:
        blocks.append({"type": "section", "text": {"type": "mrkdwn", "text": source_line}})
    blocks.extend([
        {"type": "section", "fields": fields},
        {"type": "divider"},
        context_footer(parsed),
    ])

    fallback = f"Resource deleted: {display_name} — {resource_name or 'unknown'} by {actor}"
    return {"blocks": blocks, "text": fallback}


def format_compliance_alert(parsed):
    """Untagged resource alert — Block Kit. Returns None for non-project resources."""
    detail = parsed.get("detail", {})
    resource_id = detail.get("resourceId", "unknown")
    resource_type = detail.get("resourceType", "unknown")

    if PROJECT_PREFIX not in resource_id.lower():
        return None

    compliance = detail.get("newEvaluationResult", {}).get("complianceType", "UNKNOWN")
    rule_name = detail.get("configRuleName", "unknown")
    short_type = resource_type.replace("AWS::", "").replace("::", " ")

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text", "text": ":red_circle: Untagged Resource Detected", "emoji": True}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": "A resource is missing required tags (`project`, `team`, `managed-by`)."}},
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
        context_footer(parsed),
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
        risk_emoji = {"HIGH": ":red_circle:", "MEDIUM": ":large_orange_circle:"}.get(
            risk_result.get("risk"), ":yellow_circle:")
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f":brain: *Risk Assessment* {risk_emoji} {risk_result.get('risk', '')}\n{risk_result.get('analysis', '')}"}
        })

    blocks.append({"type": "divider"})
    blocks.append(context_footer(parsed))

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
        context_footer(parsed),
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
    """Security Hub finding alert — Block Kit."""
    findings = parsed.get("detail", {}).get("findings", [])
    if not findings:
        return None

    finding = findings[0]
    title = finding.get("Title", "Security Hub Finding")
    severity_label = finding.get("Severity", {}).get("Label", "INFORMATIONAL")
    description = finding.get("Description", "")
    region = parsed.get("region", "unknown")
    account = parsed.get("account", "unknown")

    sev_emoji = {
        "CRITICAL": ":red_circle:",
        "HIGH": ":red_circle:",
        "MEDIUM": ":large_orange_circle:",
        "LOW": ":yellow_circle:",
    }.get(severity_label, ":white_circle:")

    resources = finding.get("Resources", [])
    resource_ids = [r.get("Id", "unknown").split("/")[-1] for r in resources[:3]]

    sh_url = f"https://{region}.console.aws.amazon.com/securityhub/home?region={region}#/findings"

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

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f"<{sh_url}|View in Security Hub>"}
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
# Main handler
# ---------------------------------------------------------------------------
def handler(event, context):
    for record in event["Records"]:
        sns_message = record["Sns"]
        topic_arn = sns_message.get("TopicArn", "")
        subject = sns_message.get("Subject", "AWS Alert")
        raw_message = sns_message["Message"]

        # Route to correct webhook based on SNS topic
        if topic_arn == SECURITY_TOPIC_ARN:
            webhook_url = get_webhook_url(INFRA_WEBHOOK_PARAM)
        else:
            webhook_url = get_webhook_url(COST_WEBHOOK_PARAM)

        try:
            parsed = json.loads(raw_message)
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
