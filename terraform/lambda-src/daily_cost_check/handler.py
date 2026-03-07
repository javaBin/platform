import json
import logging
import os
import urllib.request
from datetime import datetime, timedelta, timezone

import boto3

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)

ssm = boto3.client("ssm")

COST_WEBHOOK_PARAM = os.environ["COST_WEBHOOK_PARAM"]
SPIKE_THRESHOLD = float(os.environ.get("SPIKE_THRESHOLD", "1.2"))  # 20% above average
USD_TO_NOK = 10.5  # Approximate exchange rate

_webhook_cache = {}


def get_webhook_url(param_name):
    if param_name not in _webhook_cache:
        resp = ssm.get_parameter(Name=param_name, WithDecryption=True)
        _webhook_cache[param_name] = resp["Parameter"]["Value"]
    return _webhook_cache[param_name]


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


# ---------------------------------------------------------------------------
# Spike detection
# ---------------------------------------------------------------------------
def detect_spikes(yesterday_costs, avg_costs):
    """Return list of (service, yesterday, avg, pct_change) for spikes."""
    spikes = []
    for svc, cost in yesterday_costs.items():
        avg = avg_costs.get(svc, 0)
        if avg < 0.01:
            spikes.append((svc, cost, 0, None))
        elif cost > avg * SPIKE_THRESHOLD:
            pct = ((cost - avg) / avg) * 100
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


def build_alert_blocks(spikes, yesterday_date):
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
        fields = [
            {"type": "mrkdwn", "text": f"*Service*\n{svc}"},
            {"type": "mrkdwn", "text": f"*Yesterday*\n${yesterday:.2f} (~{nok:.0f} NOK)"},
        ]
        if avg > 0:
            avg_nok = avg * USD_TO_NOK
            fields.append({"type": "mrkdwn", "text": f"*7-day avg*\n${avg:.2f} (~{avg_nok:.0f} NOK)"})
        fields.append({"type": "mrkdwn", "text": f"*Change*\n{fmt_change(pct, yesterday, avg)}"})

        blocks.append({"type": "section", "fields": fields})

    blocks.append({"type": "divider"})

    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    blocks.append({
        "type": "context",
        "elements": [{"type": "mrkdwn",
                       "text": f"Source: AWS Cost Explorer | Generated {ts} | NOK rate: ~{USD_TO_NOK}"}]
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

    blocks = build_alert_blocks(spikes, yesterday)
    fallback = f"Daily cost spike: {len(spikes)} service(s) above 7-day average"

    webhook_url = get_webhook_url(COST_WEBHOOK_PARAM)
    payload = json.dumps({"blocks": blocks, "text": fallback}).encode("utf-8")
    req = urllib.request.Request(
        webhook_url, data=payload,
        headers={"Content-Type": "application/json"},
    )
    try:
        urllib.request.urlopen(req)
    except Exception as e:
        logger.error("Failed to post to Slack: %s", e)

    return {"statusCode": 200, "body": fallback}
