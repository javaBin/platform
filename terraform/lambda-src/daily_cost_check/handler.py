import json
import logging
import os
import urllib.parse
from datetime import datetime, timedelta, timezone

import boto3
from shared.athena import run_query
from shared.constants import USD_TO_NOK
from shared.slack import get_webhook_url, post_to_slack

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

COST_WEBHOOK_PARAM = os.environ["COST_WEBHOOK_PARAM"]
SPIKE_THRESHOLD = float(os.environ.get("SPIKE_THRESHOLD", "1.2"))  # 20% above average
# Minimum daily spend (USD) to qualify as a spike — filters noise on tiny amounts
MIN_SPIKE_AMOUNT = float(os.environ.get("MIN_SPIKE_AMOUNT", "10.00"))
# Minimum absolute cost difference (USD) to qualify — ignores small dollar swings
MIN_SPIKE_DELTA = float(os.environ.get("MIN_SPIKE_DELTA", "10.00"))
CUR_DATABASE = os.environ.get("CUR_DATABASE", "")
CUR_TABLE = os.environ.get("CUR_TABLE", "")
ATHENA_WORKGROUP = os.environ.get("ATHENA_WORKGROUP", "")


# ---------------------------------------------------------------------------
# Cost Explorer queries
# ---------------------------------------------------------------------------
def get_daily_costs(ce, day):
    """Fetch costs for a single day grouped by service."""
    start = str(day)
    end = str(day + timedelta(days=1))
    response = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        GroupBy=[{"Type": "DIMENSION", "Key": "SERVICE"}],
    )
    costs = {}
    for period in response["ResultsByTime"]:
        for group in period["Groups"]:
            svc = group["Keys"][0]
            amt = float(group["Metrics"]["UnblendedCost"]["Amount"])
            if amt >= 0.01:
                costs[svc] = amt
    return costs


def get_period_avg(ce, start, end):
    """Fetch average daily costs per service over a date range (end exclusive)."""
    num_days = (end - start).days
    if num_days <= 0:
        return {}
    response = ce.get_cost_and_usage(
        TimePeriod={"Start": str(start), "End": str(end)},
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
    return {svc: total / num_days for svc, total in totals.items()}


def get_usage_type_breakdown(ce, day, service):
    """Fetch usage type breakdown for a specific service on a given day."""
    start = str(day)
    end = str(day + timedelta(days=1))
    response = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        Filter={"Dimensions": {"Key": "SERVICE", "Values": [service]}},
        GroupBy=[{"Type": "DIMENSION", "Key": "USAGE_TYPE"}],
    )
    costs = {}
    for period in response["ResultsByTime"]:
        for group in period["Groups"]:
            usage_type = group["Keys"][0]
            amt = float(group["Metrics"]["UnblendedCost"]["Amount"])
            if amt >= 0.01:
                costs[usage_type] = amt
    return costs


def get_tag_breakdown(ce, day, service, tag_key="project"):
    """Fetch tag breakdown for a specific service on a given day."""
    start = str(day)
    end = str(day + timedelta(days=1))
    response = ce.get_cost_and_usage(
        TimePeriod={"Start": start, "End": end},
        Granularity="DAILY",
        Metrics=["UnblendedCost"],
        Filter={"Dimensions": {"Key": "SERVICE", "Values": [service]}},
        GroupBy=[{"Type": "TAG", "Key": tag_key}],
    )
    costs = {}
    for period in response["ResultsByTime"]:
        for group in period["Groups"]:
            tag_val = group["Keys"][0]
            if "$" in tag_val:
                tag_val = tag_val.split("$", 1)[1] or "(untagged)"
            amt = float(group["Metrics"]["UnblendedCost"]["Amount"])
            if amt >= 0.01:
                costs[tag_val] = amt
    return costs


# ---------------------------------------------------------------------------
# Cost Explorer URL builder
# ---------------------------------------------------------------------------
def cost_explorer_url(start, end, service=None):
    """Build a Cost Explorer URL, optionally filtered to a specific service."""
    params = {
        "groupBy": "UsageType" if service else "Service",
        "hasBlended": "false",
        "hasAmortized": "false",
        "excludeDiscounts": "true",
        "timeRangeOption": "Custom",
        "startDate": str(start),
        "endDate": str(end),
        "chartStyle": "Stack",
    }
    if service:
        params["filter"] = json.dumps(
            [{"dimension": "Service", "values": [service]}]
        )
    base = "https://us-east-1.console.aws.amazon.com/cost-management/home#/custom"
    return f"{base}?{urllib.parse.urlencode(params)}"


# ---------------------------------------------------------------------------
# CUR resource drilldown for spiking services
# ---------------------------------------------------------------------------
def get_spike_resources(day, service):
    """Query CUR for top resources in a spiking service. Returns list or []."""
    if not (CUR_DATABASE and CUR_TABLE and ATHENA_WORKGROUP):
        return []

    year = str(day.year)
    month = f"{day.month:02d}"
    next_day = day + timedelta(days=1)

    query = f"""
    SELECT line_item_resource_id,
           line_item_usage_type,
           COALESCE(resource_tags_user_team, '') as team,
           SUM(CAST(line_item_unblended_cost AS double)) as total_cost
    FROM "{CUR_DATABASE}"."{CUR_TABLE}"
    WHERE year = '{year}' AND month = '{month}'
      AND line_item_usage_start_date >= TIMESTAMP '{day}'
      AND line_item_usage_start_date < TIMESTAMP '{next_day}'
      AND line_item_product_code = '{service}'
      AND line_item_resource_id != ''
      AND line_item_line_item_type = 'Usage'
    GROUP BY line_item_resource_id, line_item_usage_type,
             COALESCE(resource_tags_user_team, '')
    HAVING SUM(CAST(line_item_unblended_cost AS double)) >= 0.01
    ORDER BY total_cost DESC
    LIMIT 5
    """

    return run_query(CUR_DATABASE, query, ATHENA_WORKGROUP)


def _friendly_resource_id(resource_id):
    """Shorten an ARN to a readable name."""
    if not resource_id:
        return "(no resource ID)"
    if ":::" in resource_id:
        return resource_id.rsplit(":::", 1)[-1]
    if "/" in resource_id:
        parts = resource_id.split("/")
        return parts[-1] if len(parts) <= 3 else "/".join(parts[-2:])
    if ":" in resource_id:
        return resource_id.rsplit(":", 1)[-1]
    return resource_id


# ---------------------------------------------------------------------------
# Spike detection
# ---------------------------------------------------------------------------
def detect_spikes(yesterday_costs, avg_costs):
    """Return list of (service, yesterday, avg, pct_change) for spikes.

    Filters out services where yesterday's spend is below MIN_SPIKE_AMOUNT
    or where the absolute difference is below MIN_SPIKE_DELTA to avoid
    alerting on insignificant cost changes.
    """
    spikes = []
    for svc, cost in yesterday_costs.items():
        if cost < MIN_SPIKE_AMOUNT:
            continue
        avg = avg_costs.get(svc, 0)
        if avg < 0.01:
            if cost >= MIN_SPIKE_DELTA:
                spikes.append((svc, cost, 0, None))
        elif cost > avg * SPIKE_THRESHOLD:
            delta = cost - avg
            if delta >= MIN_SPIKE_DELTA:
                pct = (delta / avg) * 100
                spikes.append((svc, cost, avg, pct))
    spikes.sort(key=lambda x: x[1] - x[2], reverse=True)
    return spikes


# ---------------------------------------------------------------------------
# Block Kit formatting
# ---------------------------------------------------------------------------
def fmt_change(pct, yesterday, avg):
    if pct is None:
        return ":new: New service (not seen in last 7 days)"
    multiplier = yesterday / avg if avg > 0 else 0
    line = f":small_red_triangle: +{pct:.0f}%"
    if multiplier >= 3:
        line += f" (:rotating_light: {multiplier:.1f}x average)"
    return line


def fmt_usage_types(usage_types, max_items=3):
    """Format top usage types as a compact mrkdwn string."""
    sorted_types = sorted(usage_types.items(), key=lambda x: x[1], reverse=True)
    lines = []
    for usage_type, cost in sorted_types[:max_items]:
        lines.append(f"  \u2022 {usage_type}: ${cost:.2f}")
    if len(sorted_types) > max_items:
        rest = sum(c for _, c in sorted_types[max_items:])
        lines.append(f"  \u2022 +{len(sorted_types) - max_items} more: ${rest:.2f}")
    return "\n".join(lines)


def build_alert_blocks(spikes, spike_details, yesterday_date):
    date_str = yesterday_date.strftime("%b %d, %Y")

    blocks = [
        {"type": "header",
         "text": {"type": "plain_text",
                  "text": ":warning: Daily Cost Spike Alert", "emoji": True}},
        {"type": "section",
         "text": {"type": "mrkdwn",
                  "text": f"Yesterday's spend ({date_str}) exceeded the 7-day average "
                          f"by >{int((SPIKE_THRESHOLD - 1) * 100)}% for these services:"}},
        {"type": "divider"},
    ]

    for svc, yesterday, avg, pct in spikes:
        nok = yesterday * USD_TO_NOK
        delta = yesterday - avg
        delta_nok = delta * USD_TO_NOK

        fields = [
            {"type": "mrkdwn", "text": f"*Service*\n{svc}"},
            {"type": "mrkdwn", "text": f"*Yesterday*\n${yesterday:.2f} (~{nok:.0f} NOK)"},
        ]
        if avg > 0:
            avg_nok = avg * USD_TO_NOK
            fields.append(
                {"type": "mrkdwn",
                 "text": f"*7-day avg*\n${avg:.2f} (~{avg_nok:.0f} NOK)"}
            )
        fields.append(
            {"type": "mrkdwn",
             "text": f"*Change*\n{fmt_change(pct, yesterday, avg)}\n+${delta:.2f} (~{delta_nok:.0f} NOK)"}
        )

        blocks.append({"type": "section", "fields": fields})

        # Usage type and tag detail for this service
        detail = spike_details.get(svc, {})
        detail_parts = []

        usage_types = detail.get("usage_types")
        if usage_types:
            detail_parts.append(f"*Top usage types:*\n{fmt_usage_types(usage_types)}")

        tags = detail.get("tags")
        if tags:
            tag_lines = ", ".join(
                f"{t}: ${c:.2f}" for t, c in
                sorted(tags.items(), key=lambda x: x[1], reverse=True)[:5]
            )
            detail_parts.append(f"*By project:* {tag_lines}")

        team_tags = detail.get("team_tags")
        if team_tags:
            team_lines = ", ".join(
                f"{t}: ${c:.2f}" for t, c in
                sorted(team_tags.items(), key=lambda x: x[1], reverse=True)[:5]
            )
            detail_parts.append(f"*By team:* {team_lines}")

        resources = detail.get("resources")
        if resources:
            res_lines = "\n".join(
                f"  \u2022 {_friendly_resource_id(r.get('line_item_resource_id', ''))}: "
                f"${float(r.get('total_cost', 0)):.2f}"
                f"{' (' + r['team'] + ')' if r.get('team') else ''}"
                for r in resources
            )
            detail_parts.append(f"*Top resources:*\n{res_lines}")

        ce_url = detail.get("url")
        if ce_url:
            detail_parts.append(f"<{ce_url}|View in Cost Explorer>")

        if detail_parts:
            blocks.append({
                "type": "context",
                "elements": [{"type": "mrkdwn", "text": "\n".join(detail_parts)}]
            })

    blocks.append({"type": "divider"})

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    overall_url = cost_explorer_url(yesterday_date, yesterday_date)
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn",
                       "text": f"<{overall_url}|View all in Cost Explorer> | Generated {ts} | "
                               f"Min cost: ${MIN_SPIKE_AMOUNT:.2f} | Min delta: ${MIN_SPIKE_DELTA:.2f} | NOK rate: ~{USD_TO_NOK}"}]
    })

    return blocks


# ---------------------------------------------------------------------------
# Lambda entry point
# ---------------------------------------------------------------------------
def handler(event, context):
    ce = boto3.client("ce", region_name="us-east-1")
    today = datetime.now(timezone.utc).date()
    yesterday = today - timedelta(days=1)

    avg_start = yesterday - timedelta(days=7)

    yesterday_costs = get_daily_costs(ce, yesterday)
    avg_costs = get_period_avg(ce, avg_start, yesterday)
    spikes = detect_spikes(yesterday_costs, avg_costs)

    if not spikes:
        logger.info("No spikes detected. Silent success.")
        return {"statusCode": 200, "body": "No spikes"}

    # Fetch usage type and tag breakdowns for each spiking service
    spike_details = {}
    for svc, _, _, _ in spikes[:5]:  # Cap detail queries to avoid API throttling
        detail = {"url": cost_explorer_url(yesterday, yesterday, service=svc)}
        try:
            detail["usage_types"] = get_usage_type_breakdown(ce, yesterday, svc)
        except Exception as e:
            logger.warning("Usage type query failed for %s: %s", svc, e)
        try:
            detail["tags"] = get_tag_breakdown(ce, yesterday, svc)
        except Exception as e:
            logger.warning("Tag query failed for %s: %s", svc, e)
        try:
            detail["team_tags"] = get_tag_breakdown(ce, yesterday, svc, tag_key="team")
        except Exception as e:
            logger.warning("Team tag query failed for %s: %s", svc, e)
        try:
            detail["resources"] = get_spike_resources(yesterday, svc)
        except Exception as e:
            logger.warning("CUR resource query failed for %s: %s", svc, e)
        spike_details[svc] = detail

    blocks = build_alert_blocks(spikes, spike_details, yesterday)
    fallback = f"Daily cost spike: {len(spikes)} service(s) above 7-day average"

    webhook_url = get_webhook_url(ssm, COST_WEBHOOK_PARAM)
    post_to_slack(webhook_url, {"blocks": blocks, "text": fallback})

    return {"statusCode": 200, "body": fallback}
