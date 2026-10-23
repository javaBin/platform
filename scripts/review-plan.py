#!/usr/bin/env python3
"""Review a Terraform plan using LLM (eu.anthropic.claude-haiku-5-5 on Bedrock).

Usage:
    python3 scripts/review-plan.py plan-output.txt

Uses AWS CLI for Bedrock Converse (no boto3 version dependency).
Writes structured JSON to REVIEW_RESULT_PATH (default: review-result.json).
"""

import json
import os
import subprocess
import sys

MODEL_ID = "eu.anthropic.claude-haiku-5-5"
REGION = os.environ.get("AWS_REGION", "eu-central-1")

REVIEW_TOOL_CONFIG = {
    "tools": [
        {
            "toolSpec": {
                "name": "submit_review",
                "description": "Submit a structured review of a Terraform plan",
                "inputSchema": {
                    "json": {
                        "type": "object",
                        "properties": {
                            "risk": {
                                "type": "string",
                                "description": "Overall risk level",
                                "enum": ["LOW", "MEDIUM", "HIGH"],
                            },
                            "summary": {
                                "type": "string",
                                "description": "One sentence summary of the plan",
                            },
                            "findings": {
                                "type": "array",
                                "description": "3-5 findings from the plan review",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "category": {
                                            "type": "string",
                                            "enum": ["security", "cost", "destruction", "routine"],
                                        },
                                        "detail": {
                                            "type": "string",
                                            "description": "Brief description of the finding",
                                        },
                                    },
                                    "required": ["category", "detail"],
                                },
                            },
                        },
                        "required": ["risk", "summary", "findings"],
                    }
                },
            }
        }
    ],
    "toolChoice": {"any": {}},
}

RISK_EMOJI = {"LOW": "\U0001f7e2", "MEDIUM": "\U0001f7e1", "HIGH": "\U0001f534"}
CATEGORY_EMOJI = {"security": "\U0001f512", "cost": "\U0001f4b0", "destruction": "\U0001f4a5", "routine": "\u2705"}


def review_plan(plan_text):
    """Send plan to Bedrock via AWS CLI for LLM review, return structured dict."""
    if len(plan_text) > 80000:
        plan_text = plan_text[:80000] + "\n\n... (truncated)"

    prompt = f"""You are a Terraform infrastructure reviewer for Javabin (Norwegian Java User Group).
Analyze this plan and submit your review.

Consider:
- Resources being created, modified, or destroyed
- Security concerns (IAM changes, SG modifications, public access)
- Cost implications (new billable resources like NAT gateways, RDS, load balancers)
- Anything unusual or dangerous (force-replacement, data loss)

Rate HIGH only for genuinely dangerous changes: destroying databases, opening security groups
to the internet, removing permission boundaries, or deleting critical infrastructure.
Routine changes (Lambda updates, tag changes, scaling) should be LOW.

Terraform plan output:
```
{plan_text}
```"""

    messages = [{"role": "user", "content": [{"text": prompt}]}]

    result = subprocess.run(
        [
            "aws", "bedrock-runtime", "converse",
            "--model-id", MODEL_ID,
            "--messages", json.dumps(messages),
            "--tool-config", json.dumps(REVIEW_TOOL_CONFIG),
            "--inference-config", json.dumps({"temperature": 0.1}),
            "--region", REGION,
        ],
        capture_output=True,
        text=True,
        timeout=120,
    )

    if result.returncode != 0:
        print(f"AWS CLI error: {result.stderr}", file=sys.stderr)
        return None

    response = json.loads(result.stdout)

    for content in response.get("output", {}).get("message", {}).get("content", []):
        if "toolUse" in content and content["toolUse"]["name"] == "submit_review":
            return content["toolUse"]["input"]

    return None


def format_review(review):
    """Format structured review as markdown for CI output / PR comment."""
    risk = review["risk"]
    summary = review["summary"]
    findings = review.get("findings", [])

    emoji = RISK_EMOJI.get(risk, "\u26aa")
    lines = [f"**Risk: {emoji} {risk}**", "", summary, ""]

    for f in findings:
        cat_emoji = CATEGORY_EMOJI.get(f["category"], "\u2022")
        lines.append(f"- {cat_emoji} **[{f['category']}]** {f['detail']}")

    return "\n".join(lines)


def write_result(review):
    """Write review result to JSON file for the apply job to read."""
    result_path = os.environ.get("REVIEW_RESULT_PATH", "review-result.json")
    with open(result_path, "w") as f:
        json.dump(review or {"risk": "FAILED"}, f)


def main():
    if len(sys.argv) < 2:
        print("Usage: python3 review-plan.py <plan-output-file>", file=sys.stderr)
        sys.exit(1)

    with open(sys.argv[1]) as f:
        plan_text = f.read()

    if not plan_text.strip():
        print("Empty plan output, skipping review.")
        write_result({"risk": "LOW", "summary": "Empty plan", "findings": []})
        return

    try:
        review = review_plan(plan_text)
        if not review:
            print("LLM returned no structured output.", file=sys.stderr)
            write_result(None)
            return

        print(format_review(review))
        write_result(review)

    except Exception as e:
        print(f"LLM review failed: {e}", file=sys.stderr)
        write_result(None)


if __name__ == "__main__":
    main()
