"""Shared AWS Pricing API utilities.

Used by slack_alert and plan review to estimate costs of newly created resources.
All prices are fetched at runtime from the Pricing API — nothing is hardcoded.
"""

import json
import logging
import os

import boto3

logger = logging.getLogger(__name__)

DEPLOY_REGION = os.environ.get("DEPLOY_REGION", "eu-central-1")

# Cache prices across invocations within the same Lambda container
_pricing_cache = {}


def _get_hourly_price(service_code, product_family, region=None):
    """Look up hourly price from the Pricing API. Returns float or None."""
    region = region or DEPLOY_REGION
    cache_key = f"{service_code}:{product_family}:{region}"
    if cache_key in _pricing_cache:
        return _pricing_cache[cache_key]
    try:
        pricing = boto3.client("pricing", region_name="us-east-1")
        resp = pricing.get_products(
            ServiceCode=service_code,
            Filters=[
                {"Type": "TERM_MATCH", "Field": "productFamily", "Value": product_family},
                {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
            ],
            MaxResults=5,
        )
        for item in resp.get("PriceList", []):
            data = json.loads(item)
            for offer in data.get("terms", {}).get("OnDemand", {}).values():
                for dim in offer.get("priceDimensions", {}).values():
                    if dim.get("unit", "").startswith("Hrs"):
                        price = float(dim["pricePerUnit"].get("USD", "0"))
                        if price > 0:
                            _pricing_cache[cache_key] = price
                            return price
    except Exception as e:
        logger.error("Pricing API lookup failed for %s: %s", cache_key, e)
    _pricing_cache[cache_key] = None
    return None


def _get_instance_price(instance_type, region=None):
    """Look up EC2 on-demand hourly price for a specific instance type."""
    region = region or DEPLOY_REGION
    cache_key = f"ec2:{instance_type}:{region}"
    if cache_key in _pricing_cache:
        return _pricing_cache[cache_key]
    try:
        pricing = boto3.client("pricing", region_name="us-east-1")
        resp = pricing.get_products(
            ServiceCode="AmazonEC2",
            Filters=[
                {"Type": "TERM_MATCH", "Field": "instanceType", "Value": instance_type},
                {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
                {"Type": "TERM_MATCH", "Field": "operatingSystem", "Value": "Linux"},
                {"Type": "TERM_MATCH", "Field": "tenancy", "Value": "Shared"},
                {"Type": "TERM_MATCH", "Field": "preInstalledSw", "Value": "NA"},
                {"Type": "TERM_MATCH", "Field": "capacitystatus", "Value": "Used"},
            ],
            MaxResults=3,
        )
        for item in resp.get("PriceList", []):
            data = json.loads(item)
            for offer in data.get("terms", {}).get("OnDemand", {}).values():
                for dim in offer.get("priceDimensions", {}).values():
                    price = float(dim["pricePerUnit"].get("USD", "0"))
                    if price > 0:
                        _pricing_cache[cache_key] = price
                        return price
    except Exception as e:
        logger.error("Pricing API lookup failed for %s: %s", cache_key, e)
    _pricing_cache[cache_key] = None
    return None


def _get_usage_price(service_code, filters, unit_prefix, region=None):
    """Look up per-unit price from the Pricing API for usage-based services.

    Returns float or None. unit_prefix matches the start of the price
    dimension unit (e.g. 'Requests', 'GB-Mo', 'Second').
    """
    region = region or DEPLOY_REGION
    cache_key = f"{service_code}:{unit_prefix}:{region}"
    if cache_key in _pricing_cache:
        return _pricing_cache[cache_key]
    try:
        pricing = boto3.client("pricing", region_name="us-east-1")
        all_filters = [
            {"Type": "TERM_MATCH", "Field": "regionCode", "Value": region},
        ] + [{"Type": "TERM_MATCH", "Field": k, "Value": v} for k, v in filters]
        resp = pricing.get_products(
            ServiceCode=service_code, Filters=all_filters, MaxResults=5,
        )
        for item in resp.get("PriceList", []):
            data = json.loads(item)
            for offer in data.get("terms", {}).get("OnDemand", {}).values():
                for dim in offer.get("priceDimensions", {}).values():
                    if dim.get("unit", "").startswith(unit_prefix):
                        price = float(dim["pricePerUnit"].get("USD", "0"))
                        if price > 0:
                            _pricing_cache[cache_key] = price
                            return price
    except Exception as e:
        logger.error("Pricing API lookup failed for %s: %s", cache_key, e)
    _pricing_cache[cache_key] = None
    return None


# Usage-based pricing queries: keyed by CloudTrail event name (prefix-matched).
# Each entry: (service_code, filters, unit_prefix, scale, label)
_USAGE_PRICING_QUERIES = {
    "CreateFunction": [
        ("AWSLambda", [("group", "AWS-Lambda-Requests")], "Request", 1e6, "/1M req"),
        ("AWSLambda", [("group", "AWS-Lambda-Duration")], "Lambda-GB", 1, "/GB-sec"),
    ],
    "CreateBucket": [
        ("AmazonS3", [("productFamily", "Storage"), ("storageClass", "General Purpose")], "GB-Mo", 1, "/GB/mo"),
    ],
    "CreateTable": [
        ("AmazonDynamoDB", [("productFamily", "Amazon DynamoDB PayPerRequest Throughput"), ("group", "DDB-WriteUnits")], "WriteRequestUnits", 1e6, "/1M writes"),
        ("AmazonDynamoDB", [("productFamily", "Amazon DynamoDB PayPerRequest Throughput"), ("group", "DDB-ReadUnits")], "ReadRequestUnits", 1e6, "/1M reads"),
    ],
}


def estimate_cost(event_name, detail):
    """Return a cost estimate string using the Pricing API, or None."""
    request = detail.get("requestParameters") or {}
    region = detail.get("awsRegion", DEPLOY_REGION)

    if event_name == "CreateNatGateway":
        hourly = _get_hourly_price("AmazonEC2", "NAT Gateway", region)
        if hourly:
            return f"~${hourly * 730:.0f}/mo + data transfer"

    elif event_name == "CreateLoadBalancer":
        hourly = _get_hourly_price("ElasticLoadBalancing", "Load Balancer-Application", region)
        if hourly:
            return f"~${hourly * 730:.0f}/mo + LCU charges"

    elif event_name == "RunInstances":
        instance_type = request.get("instanceType")
        if instance_type:
            hourly = _get_instance_price(instance_type, region)
            if hourly:
                return f"~${hourly * 730:.2f}/mo ({instance_type})"
            return f"check pricing for {instance_type}"

    elif event_name == "CreateDBInstance":
        db_class = request.get("dBInstanceClass")
        if db_class:
            return f"check pricing for {db_class}"

    # Usage-based resources
    for prefix, queries in _USAGE_PRICING_QUERIES.items():
        if event_name.startswith(prefix):
            parts = []
            for svc, filters, unit, scale, label in queries:
                price = _get_usage_price(svc, filters, unit, region)
                if price:
                    val = price * scale
                    if val >= 1:
                        parts.append(f"${val:.2f}{label}")
                    elif val >= 0.01:
                        parts.append(f"${val:.3f}{label}")
                    else:
                        parts.append(f"${val:.6f}{label}")
            return ", ".join(parts) if parts else None

    return None
