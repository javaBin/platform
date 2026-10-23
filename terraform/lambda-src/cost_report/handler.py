import json
import logging
import os
import urllib.parse
from calendar import monthrange
from datetime import date, datetime, timedelta, timezone

import boto3
from botocore.config import Config
from shared.athena import run_query
from shared.constants import USD_TO_NOK, risk_emoji
from shared.slack import get_webhook_url, post_to_slack

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

COST_WEBHOOK_PARAM = os.environ["COST_WEBHOOK_PARAM"]
CUR_DATABASE = os.environ.get("CUR_DATABASE", "")
CUR_TABLE = os.environ.get("CUR_TABLE", "")
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "")

# ---------------------------------------------------------------------------
# Service categorisation — pattern-based, not hardcoded lists
# ---------------------------------------------------------------------------
AI_KEYWORDS = [
    "bedrock", "claude", "anthropic", "gpt", "gemini",
    "titan", "llama", "mistral", "ai21", "cohere",
]

# Threshold below which services are grouped into "Other"
OTHER_THRESHOLD = 0.50


# ---------------------------------------------------------------------------
# Date helpers
# ---------------------------------------------------------------------------
def last_complete_week(ref_date):
    """Return (monday, sunday) of the last fully completed Mon-Sun week."""
    days_since_monday = ref_date.weekday()  # Mon=0
    last_monday = ref_date - timedelta(days=days_since_monday + 7)
    last_sunday = last_monday + timedelta(days=6)
    return last_monday, last_sunday


def month_start(d):
    return d.replace(day=1)


def prev_month_start(d):
    first = month_start(d)
    return (first - timedelta(days=1)).replace(day=1)


def days_in_month(d):
    return monthrange(d.year, d.month)[1]


# ---------------------------------------------------------------------------
# Cost Explorer queries
# ---------------------------------------------------------------------------
def get_costs_by_service(ce, start, end):
    """Fetch costs grouped by service. `end` is INCLUSIVE — we add +1 for the API."""
    end_exclusive = str(end + timedelta(days=1))
    response = ce.get_cost_and_usage(
        TimePeriod={"Start": str(start), "End": end_exclusive},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    totals = {}
    for period in response["ResultsByTime"]:
        for group in period["Groups"]:
            svc = group["Keys"][0]
            amt = float(group["Metrics"]["UnblendedCost"]["Amount"])
            totals[svc] = totals.get(svc, 0) + amt
    return totals


def get_costs_by_tag(ce, start, end, tag_key="project"):
    """Fetch costs grouped by a cost allocation tag (e.g. project)."""
    end_exclusive = str(end + timedelta(days=1))
    response = ce.get_cost_and_usage(
        TimePeriod={"Start": str(start), "End": end_exclusive},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "TAG", "Key": tag_key}],
    )
    totals = {}
    for period in response["ResultsByTime"]:
        for group in period["Groups"]:
            tag_val = group["Keys"][0]
            # Tag key is "project$value" — extract the value part
            if "$" in tag_val:
                tag_val = tag_val.split("$", 1)[1] or "(untagged)"
            amt = float(group["Metrics"]["UnblendedCost"]["Amount"])
            totals[tag_val] = totals.get(tag_val, 0) + amt
    return totals


# ---------------------------------------------------------------------------
# Categorise services
# ---------------------------------------------------------------------------
def categorise_service(name):
    """Pattern-based: new AI services are caught automatically."""
    if any(kw in name.lower() for kw in AI_KEYWORDS):
        return "ai_models"
    return "infrastructure"


def categorise(service_costs):
    ai, infra, other = {}, {}, {}
    for svc, cost in service_costs.items():
        if cost < 0.01:
            continue
        if cost < OTHER_THRESHOLD:
            other[svc] = cost
        elif categorise_service(svc) == "ai_models":
            ai[svc] = cost
        else:
            infra[svc] = cost
    return ai, infra, other


# ---------------------------------------------------------------------------
# Cost Explorer URL builder
# ---------------------------------------------------------------------------
def cost_explorer_url(start, end):
    base = "https://us-east-1.console.aws.amazon.com/cost-management/home#/custom"
    params = urllib.parse.urlencode({
        "groupBy": "Service",
        "hasBlended": "false",
        "hasAmortized": "false",
        "excludeDiscounts": "true",
        "timeRangeOption": "Custom",
        "startDate": str(start),
        "endDate": str(end),
        "chartStyle": "Stack",
    })
    return f"{base}?{params}"

LLM_MODEL_ID = "eu.anthropic.claude-haiku-5-5"
LLM_REGION = os.environ.get("DEPLOY_REGION", "eu-central-1")

MAX_SERVICES_PER_CATEGORY = 8
SPIKE_THRESHOLD_PCT = 15.0


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------
def fmt_date(d):
    return d.strftime("%b %d")


def fmt_usd_nok(usd):
    """Format amount in both USD and NOK."""
    nok = usd * USD_TO_NOK
    return f"${usd:.2f} (~{nok:.0f} NOK)"


def build_service_table(services, week_total, prev_week=None):
    """Build a Block Kit table block for a category of services."""
    sorted_svcs = sorted(services.items(), key=lambda x: x[1], reverse=True)

    has_prev = prev_week is not None
    header = [
        {"type": "raw_text", "text": "Service"},
        {"type": "raw_text", "text": "Amount"},
        {"type": "raw_text", "text": "%"},
    ]
    if has_prev:
        header.append({"type": "raw_text", "text": "WoW"})

    rows = [header]
    for i, (svc, cost) in enumerate(sorted_svcs):
        if i >= MAX_SERVICES_PER_CATEGORY:
            remaining = len(sorted_svcs) - MAX_SERVICES_PER_CATEGORY
            remaining_cost = sum(c for _, c in sorted_svcs[MAX_SERVICES_PER_CATEGORY:])
            row = [
                {"type": "raw_text", "text": f"+{remaining} more"},
                {"type": "raw_text", "text": f"${remaining_cost:.2f}"},
                {"type": "raw_text", "text": ""},
            ]
            if has_prev:
                row.append({"type": "raw_text", "text": ""})
            rows.append(row)
            break

        pct = (cost / week_total * 100) if week_total > 0 else 0
        nok = cost * USD_TO_NOK
        row = [
            {"type": "raw_text", "text": svc},
            {"type": "raw_text", "text": f"${cost:.2f} (~{nok:.0f} NOK)"},
            {"type": "raw_text", "text": f"{pct:.1f}%"},
        ]
        if has_prev:
            prev_cost = prev_week.get(svc, 0)
            delta = cost - prev_cost
            if abs(delta) < 0.01:
                row.append({"type": "raw_text", "text": "-"})
            elif delta > 0:
                row.append({"type": "raw_text", "text": f"+${delta:.2f}"})
            else:
                row.append({"type": "raw_text", "text": f"-${abs(delta):.2f}"})
        rows.append(row)

    col_settings = [
        {"is_wrapped": True},
        {"align": "right"},
        {"align": "right"},
    ]
    if has_prev:
        col_settings.append({"align": "right"})

    return {
        "type": "table",
        "column_settings": col_settings,
        "rows": rows,
    }


def fmt_change(current, previous):
    if previous == 0:
        return "+${:.2f} (new)".format(current) if current > 0 else "No change"
    delta = current - previous
    pct = (delta / previous) * 100
    if abs(pct) < 1.0:
        return f":white_circle: {'+' if delta >= 0 else '-'}${abs(delta):.2f} ({pct:+.1f}%)"
    elif delta > 0:
        return f":small_red_triangle: +${delta:.2f} (+{pct:.1f}%)"
    else:
        return f":small_red_triangle_down: -${abs(delta):.2f} ({pct:.1f}%)"


# ---------------------------------------------------------------------------
# LLM narrative generation via Bedrock — structured output
# ---------------------------------------------------------------------------
_NARRATIVE_TOOL = {
    "tools": [{
        "toolSpec": {
            "name": "submit_narrative",
            "description": "Submit a cost report narrative summary",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "summary": {
                            "type": "string",
                            "description": "2-3 sentence executive summary of the cost report",
                        },
                        "notable": {
                            "type": "boolean",
                            "description": "True if there is something worth calling out "
                            "(significant change, unusual pattern). False if costs are stable.",
                        },
                    },
                    "required": ["summary", "notable"],
                }
            },
        }
    }],
    "toolChoice": {"any": {}},
}

_SPIKE_TOOL = {
    "tools": [{
        "toolSpec": {
            "name": "submit_spike_analysis",
            "description": "Submit a root cause analysis for a cost spike",
            "inputSchema": {
                "json": {
                    "type": "object",
                    "properties": {
                        "root_cause": {
                            "type": "string",
                            "description": "2-3 sentence explanation of the likely root cause",
                        },
                        "suggested_action": {
                            "type": "string",
                            "description": "One concrete action to investigate",
                        },
                        "severity": {
                            "type": "string",
                            "enum": ["LOW", "MEDIUM", "HIGH"],
                            "description": "LOW = expected growth, MEDIUM = worth investigating, "
                            "HIGH = likely anomaly needing immediate attention",
                        },
                    },
                    "required": ["root_cause", "suggested_action", "severity"],
                }
            },
        }
    }],
    "toolChoice": {"any": {}},
}


def _llm_structured(prompt, tool_config):
    """Call Bedrock with tool use, return structured dict or None."""
    try:
        bedrock = boto3.client(
            "bedrock-runtime", region_name=LLM_REGION,
            config=Config(read_timeout=20, connect_timeout=5),
        )
        response = bedrock.converse(
            modelId=LLM_MODEL_ID,
            messages=[{"role": "user", "content": [{"text": prompt}]}],
            toolConfig=tool_config,
            inferenceConfig={"temperature": 0.2},
        )
        for content in response["output"]["message"]["content"]:
            if "toolUse" in content:
                return content["toolUse"]["input"]
    except Exception as e:
        logger.warning("LLM call failed: %s", e)
    return None


def generate_narrative(this_week, prev_week, mtd, tw_total, pw_total,
                       mtd_total, projected, month_name, resource_summary=""):
    """Generate executive summary. Returns dict with summary/notable or None."""
    top_services = sorted(this_week.items(), key=lambda x: x[1], reverse=True)[:8]
    svc_summary = ", ".join(f"{s}: ${c:.2f}" for s, c in top_services)

    movers = []
    for svc, cost in this_week.items():
        prev_cost = prev_week.get(svc, 0)
        delta = cost - prev_cost
        if abs(delta) > 1.0:
            movers.append((svc, delta, cost, prev_cost))
    movers.sort(key=lambda x: abs(x[1]), reverse=True)
    mover_lines = "; ".join(
        f"{s}: ${c:.2f} (was ${p:.2f}, delta ${d:+.2f})"
        for s, d, c, p in movers[:5]
    )

    wow_pct = ((tw_total - pw_total) / pw_total * 100) if pw_total > 0 else 0

    prompt = f"""You are an AWS cost analyst for Javabin (Norwegian Java User Group). Write an executive summary for this weekly cost report.

Total this week: ${tw_total:.2f} | Previous week: ${pw_total:.2f} | WoW: {wow_pct:+.1f}%
MTD: ${mtd_total:.2f} | Projected {month_name}: ${projected:.2f}

Top services: {svc_summary}
Biggest movers WoW: {mover_lines or 'no significant changes'}
{resource_summary}

Be specific about which services and resources drove changes. Set notable=true only if there is a meaningful change worth highlighting, false if costs are stable week-over-week."""
    return _llm_structured(prompt, _NARRATIVE_TOOL)


def analyze_spike(this_week, prev_week, tw_total, pw_total):
    """If WoW increase exceeds threshold, generate root cause analysis."""
    if pw_total <= 0:
        return None
    wow_pct = (tw_total - pw_total) / pw_total * 100
    if wow_pct < SPIKE_THRESHOLD_PCT:
        return None

    increases = []
    for svc, cost in this_week.items():
        prev_cost = prev_week.get(svc, 0)
        delta = cost - prev_cost
        if delta > 0.50:
            increases.append((svc, delta, cost, prev_cost))
    increases.sort(key=lambda x: x[1], reverse=True)
    increase_lines = "\n".join(
        f"- {s}: ${c:.2f} (was ${p:.2f}, +${d:.2f})"
        for s, d, c, p in increases[:8]
    )

    prompt = f"""You are an AWS cost analyst for Javabin. Weekly costs spiked {wow_pct:+.1f}% (${pw_total:.2f} -> ${tw_total:.2f}).

Services with biggest increases:
{increase_lines}

Explain the likely root causes. Consider: new resources, data transfer, infrastructure scaling."""
    return _llm_structured(prompt, _SPIKE_TOOL)


# ---------------------------------------------------------------------------
# CUR resource-level drilldown via Athena
# ---------------------------------------------------------------------------
def _friendly_resource_id(resource_id):
    """Shorten an ARN to a readable name."""
    if not resource_id:
        return "(no resource ID)"
    # Strip common ARN prefix, keep the useful tail
    if "::" in resource_id:
        # S3 bucket: arn:aws:s3:::bucket-name → bucket-name
        return resource_id.rsplit(":::", 1)[-1]
    if "/" in resource_id:
        # ECS/Lambda/etc: ...service/cluster/name → name
        parts = resource_id.split("/")
        return parts[-1] if len(parts) <= 3 else "/".join(parts[-2:])
    if ":" in resource_id:
        return resource_id.rsplit(":", 1)[-1]
    return resource_id


def get_resource_drilldown(week_start, week_end):
    """Query CUR via Athena for top resources this week. Returns dict or None."""
    if not (CUR_DATABASE and CUR_TABLE and ATHENA_WORKGROUP):
        return None

    year = str(week_start.year)
    month = f"{week_start.month:02d}"

    # Top 10 resources overall
    top_query = f"""
    SELECT line_item_resource_id,
           line_item_product_code,
           COALESCE(resource_tags_user_team, '') as team,
           SUM(CAST(line_item_unblended_cost AS double)) as total_cost
    FROM "{CUR_DATABASE}"."{CUR_TABLE}"
    WHERE year = '{year}' AND month = '{month}'
      AND line_item_usage_start_date >= TIMESTAMP '{week_start}'
      AND line_item_usage_start_date < TIMESTAMP '{week_end + timedelta(days=1)}'
      AND line_item_resource_id != ''
      AND line_item_line_item_type = 'Usage'
    GROUP BY line_item_resource_id, line_item_product_code,
             COALESCE(resource_tags_user_team, '')
    HAVING SUM(CAST(line_item_unblended_cost AS double)) >= 0.01
    ORDER BY total_cost DESC
    LIMIT 10
    """

    top_resources = run_query(CUR_DATABASE, top_query, ATHENA_WORKGROUP)
    if not top_resources:
        return None

    return {"top_resources": top_resources}


def build_resource_drilldown_blocks(drilldown):
    """Build Block Kit blocks for resource-level cost drilldown."""
    blocks = []
    top = drilldown.get("top_resources", [])
    if not top:
        return blocks

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": ":mag: *Resource-Level Drilldown*"}
    })

    header = [
        {"type": "raw_text", "text": "Resource"},
        {"type": "raw_text", "text": "Service"},
        {"type": "raw_text", "text": "Team"},
        {"type": "raw_text", "text": "Cost"},
    ]
    rows = [header]

    for item in top:
        cost = float(item.get("total_cost", 0))
        nok = cost * USD_TO_NOK
        rows.append([
            {"type": "raw_text", "text": _friendly_resource_id(item.get("line_item_resource_id", ""))},
            {"type": "raw_text", "text": item.get("line_item_product_code", "")},
            {"type": "raw_text", "text": item.get("team", "(untagged)")},
            {"type": "raw_text", "text": f"${cost:.2f} (~{nok:.0f} NOK)"},
        ])

    blocks.append({
        "type": "table",
        "column_settings": [
            {"is_wrapped": True},
            {},
            {},
            {"align": "right"},
        ],
        "rows": rows,
    })

    return blocks


# ---------------------------------------------------------------------------
# Block Kit builder
# ---------------------------------------------------------------------------
def build_blocks(this_week, prev_week, mtd, prev_mtd, project_costs, team_costs,
                 tw_start, tw_end, pw_start, pw_end,
                 mtd_start_d, mtd_end_d, today):
    blocks = []

    blocks.append({
        "type": "header",
        "text": {"type": "plain_text", "text": ":bar_chart: Weekly Cost Report", "emoji": True}
    })
    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn",
                 "text": f"*{fmt_date(tw_start)} – {fmt_date(tw_end)}, {tw_end.year}*"}
    })
    blocks.append({"type": "divider"})

    tw_total = sum(c for c in this_week.values() if c >= 0.01)
    pw_total = sum(c for c in prev_week.values() if c >= 0.01)

    mtd_total = sum(c for c in mtd.values() if c >= 0.01)
    prev_mtd_total = sum(c for c in prev_mtd.values() if c >= 0.01)
    mtd_days = (mtd_end_d - mtd_start_d).days
    total_days_in_month = days_in_month(mtd_start_d)
    projected = (mtd_total / mtd_days * total_days_in_month) if mtd_days > 0 else 0
    curr_month_name = mtd_start_d.strftime("%B")

    # Per-project tag breakdown as table
    if project_costs:
        filtered = {k: v for k, v in project_costs.items() if v >= 0.01}
        if filtered:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": ":label: *By Project Tag*"}
            })
            blocks.append(build_service_table(filtered, tw_total))

    # Per-team tag breakdown as table
    if team_costs:
        filtered_teams = {k: v for k, v in team_costs.items() if v >= 0.01}
        if filtered_teams:
            blocks.append({
                "type": "section",
                "text": {"type": "mrkdwn", "text": ":busts_in_silhouette: *By Team Tag*"}
            })
            blocks.append(build_service_table(filtered_teams, tw_total))

    # Categorised service breakdown as tables
    ai, infra, other = categorise(this_week)
    prev_ai, prev_infra, prev_other = categorise(prev_week)

    ce_url = cost_explorer_url(tw_start, tw_end)

    if ai:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":brain: *AI Models*"}
        })
        blocks.append(build_service_table(ai, tw_total, prev_ai))
    if infra:
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn", "text": f":cloud: *Infrastructure*"}
        })
        blocks.append(build_service_table(infra, tw_total, prev_infra))
    if other:
        other_total = sum(other.values())
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f":envelope: *Other (< ${OTHER_THRESHOLD:.2f})* \u2014 ${other_total:.2f} total"}
        })
        blocks.append(build_service_table(other, tw_total))

    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn", "text": f"<{ce_url}|View in Cost Explorer>"}]
    })

    blocks.append({"type": "divider"})

    num_days = (tw_end - tw_start).days + 1
    daily_avg = tw_total / num_days if num_days else 0

    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*Total This Week*\n{fmt_usd_nok(tw_total)}"},
            {"type": "mrkdwn", "text": f"*Previous Week*\n{fmt_usd_nok(pw_total)}"},
            {"type": "mrkdwn", "text": f"*Week-over-Week*\n{fmt_change(tw_total, pw_total)}"},
            {"type": "mrkdwn", "text": f"*Avg Daily*\n{fmt_usd_nok(daily_avg)}"},
            {"type": "mrkdwn", "text": f"*Month-to-Date*\n{fmt_usd_nok(mtd_total)}"},
            {"type": "mrkdwn", "text": f"*Projected {curr_month_name}*\n{fmt_usd_nok(projected)}"},
        ]
    })

    blocks.append({"type": "divider"})

    prev_month_name = prev_month_start(today).strftime("%B")

    blocks.append({
        "type": "section",
        "text": {"type": "mrkdwn", "text": f":calendar: *Month-to-Date ({curr_month_name})*"}
    })
    blocks.append({
        "type": "section",
        "fields": [
            {"type": "mrkdwn", "text": f"*MTD Total*\n{fmt_usd_nok(mtd_total)}"},
            {"type": "mrkdwn", "text": f"*{prev_month_name} Same Period*\n{fmt_usd_nok(prev_mtd_total)}"},
            {"type": "mrkdwn", "text": f"*MTD Change*\n{fmt_change(mtd_total, prev_mtd_total)}"},
            {"type": "mrkdwn", "text": f"*Projected {curr_month_name}*\n{fmt_usd_nok(projected)}"},
        ]
    })

    blocks.append({"type": "divider"})

    # Resource-level drilldown from CUR (graceful — skipped if unavailable)
    drilldown = get_resource_drilldown(tw_start, tw_end)
    if drilldown:
        blocks.extend(build_resource_drilldown_blocks(drilldown))
        blocks.append({"type": "divider"})
    else:
        logger.info("CUR resource drilldown unavailable — skipping")

    # LLM spike root cause (shown as a section — important)
    spike_result = analyze_spike(this_week, prev_week, tw_total, pw_total)
    if spike_result:
        sev_emoji = risk_emoji(spike_result.get("severity"))
        blocks.append({
            "type": "section",
            "text": {"type": "mrkdwn",
                     "text": f":warning: *Spike Analysis* {sev_emoji}\n{spike_result.get('root_cause', '')}\n\n"
                     f"_Suggested action: {spike_result.get('suggested_action', 'N/A')}_"}
        })
        blocks.append({"type": "divider"})

    # LLM narrative + footer as context blocks
    resource_summary = ""
    if drilldown:
        top_res = drilldown.get("top_resources", [])[:5]
        resource_summary = "\nTop resources by cost: " + ", ".join(
            f"{_friendly_resource_id(r.get('line_item_resource_id',''))} "
            f"({r.get('line_item_product_code','')}, {r.get('team','?')}): "
            f"${float(r.get('total_cost',0)):.2f}"
            for r in top_res
        )
    narrative_result = generate_narrative(
        this_week, prev_week, mtd,
        tw_total, pw_total, mtd_total, projected, curr_month_name,
        resource_summary=resource_summary,
    )

    ce_url = cost_explorer_url(tw_start, tw_end)
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    context_parts = []
    if narrative_result:
        context_parts.append(
            {"type": "mrkdwn", "text": f":brain: {narrative_result.get('summary', '')}"}
        )
    context_parts.append(
        {"type": "mrkdwn", "text": f"<{ce_url}|View in Cost Explorer> | Generated {ts} | NOK rate: ~{USD_TO_NOK}"}
    )
    blocks.append({"type": "context", "elements": context_parts})

    return blocks


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------
def handler(event, context):
    ce = boto3.client("ce", region_name="us-east-1")
    today = datetime.now(timezone.utc).date()

    tw_start, tw_end = last_complete_week(today)
    pw_start = tw_start - timedelta(days=7)
    pw_end = tw_end - timedelta(days=7)

    mtd_start_d = month_start(today)
    mtd_end_d = today - timedelta(days=1)

    pm_start = prev_month_start(today)
    pm_day = min((mtd_end_d - mtd_start_d).days, days_in_month(pm_start) - 1)
    pm_end = pm_start + timedelta(days=pm_day)

    # Fetch cost data (6 API calls: 4 by-service + 2 by-tag)
    this_week = get_costs_by_service(ce, tw_start, tw_end)
    prev_week = get_costs_by_service(ce, pw_start, pw_end)
    mtd = get_costs_by_service(ce, mtd_start_d, mtd_end_d)
    prev_mtd = get_costs_by_service(ce, pm_start, pm_end)
    project_costs = get_costs_by_tag(ce, tw_start, tw_end, "project")
    team_costs = get_costs_by_tag(ce, tw_start, tw_end, "team")

    blocks = build_blocks(
        this_week, prev_week, mtd, prev_mtd, project_costs, team_costs,
        tw_start, tw_end, pw_start, pw_end,
        mtd_start_d, mtd_end_d, today,
    )

    tw_total = sum(c for c in this_week.values() if c >= 0.01)
    fallback = f"Weekly Cost Report: ${tw_total:.2f} ({fmt_date(tw_start)}–{fmt_date(tw_end)})"

    webhook_url = get_webhook_url(ssm, COST_WEBHOOK_PARAM)
    post_to_slack(webhook_url, {"blocks": blocks, "text": fallback})

    return {"statusCode": 200, "body": fallback}
