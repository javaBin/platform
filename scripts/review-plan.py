#!/usr/bin/env python3
"""Review a Terraform plan using LLM (eu.anthropic.claude-haiku-5-5 on Bedrock).

Usage:
    python3 scripts/review-plan.py plan-output.txt

Uses AWS CLI for Bedrock Converse (no boto3 version dependency).
Writes structured JSON to REVIEW_RESULT_PATH (default: review-result.json).

Also bundled into the javabin-plan-review Lambda (app plans), which passes
its own boto3-based `converse` to review_plan().
"""

import json
import os
import re
import subprocess
import sys
import tempfile

MODEL_ID = "eu.anthropic.claude-haiku-5-5"
REGION = os.environ.get("AWS_REGION", "eu-central-1")

# No input cap by default: the whole plan goes to the model (Haiku 5.5 has a
# 1M-token context; plan text is roughly 3-4 characters per token). A fixed
# character cap is what kept cutting real plans - first 80k, then 400k cut a
# 231-destroy plan (830k chars; destroy diffs print every attribute) to 28 of
# its changes. REVIEW_MAX_PLAN_CHARS can still set a cap; a plan above it gets
# a complete change list plus as much diff as fits (see build_plan_section),
# never a silent cut.
MAX_PLAN_CHARS = int(os.environ["REVIEW_MAX_PLAN_CHARS"]) if os.environ.get("REVIEW_MAX_PLAN_CHARS") else None

# State refresh / data source read lines: noise for review purposes. Resource
# addresses start in column 0; diff content is always indented.
REFRESH_LINE = re.compile(r"^\S.*: (Refreshing state\.\.\.|Reading\.\.\.|Still reading\.\.\.|Read complete after )")

# Resource change headers, e.g. "  # aws_iam_role.x will be updated in-place".
CHANGE_HEADER = re.compile(
    r"^\s{0,2}# ([^\s(].*?)(?: \(deposed object \w+\))?(?: is tainted, so)? "
    r"(will be .+|will no longer be managed by Terraform|must be replaced|has moved to .+)$")

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


def cli_converse(messages, tool_config):
    """Bedrock Converse via the AWS CLI. Returns the response dict or None.

    Arguments go through files: a whole plan is far larger than the kernel's
    per-argument limit (128 KiB on Linux).
    """
    with tempfile.TemporaryDirectory() as tmp:
        msg_path = os.path.join(tmp, "messages.json")
        tools_path = os.path.join(tmp, "tool-config.json")
        with open(msg_path, "w") as f:
            json.dump(messages, f)
        with open(tools_path, "w") as f:
            json.dump(tool_config, f)
        result = subprocess.run(
            [
                "aws", "bedrock-runtime", "converse",
                "--model-id", MODEL_ID,
                "--messages", f"file://{msg_path}",
                "--tool-config", f"file://{tools_path}",
                "--region", REGION,
                "--cli-read-timeout", "300",
            ],
            capture_output=True,
            text=True,
            timeout=320,
        )
    if result.returncode != 0:
        print(f"AWS CLI error: {result.stderr}", file=sys.stderr)
        return None
    return json.loads(result.stdout)


def strip_refresh_noise(plan_text):
    """Drop state refresh / data source read lines; keep everything else."""
    return "\n".join(line for line in plan_text.splitlines() if not REFRESH_LINE.match(line))


def resource_changes(plan_text):
    """Every resource change header in the plan, as (line_index, address, action)."""
    changes = []
    for i, line in enumerate(plan_text.splitlines()):
        m = CHANGE_HEADER.match(line)
        if m:
            changes.append((i, m.group(1), m.group(2)))
    return changes


INSTANCE_KEY = re.compile(r"\[[^\]]*\]")


def _grouped_section(clean, changes, totals, limit):
    """For plans whose change list alone is too large: group changes by
    address with instance keys removed (aws_x.y[*]: will be created ×25)."""
    groups = {}
    for _, addr, action in changes:
        key = (INSTANCE_KEY.sub("[*]", addr), action)
        groups[key] = groups.get(key, 0) + 1
    lines = [f"- {addr}: {action}" + (f" ×{n}" if n > 1 else "") for (addr, action), n in groups.items()]
    shown, size = [], 0
    for line in lines:
        if size + len(line) + 1 > limit - 2_000:
            break
        shown.append(line)
        size += len(line) + 1
    dropped = len(lines) - len(shown)
    notice = (f"NOTE: this plan has {len(changes)} resource changes and {len(clean):,} characters, too large "
              f"to show its diff within the {limit:,}-character review limit. No diff is shown. The list "
              f"below groups the changes by address (instance keys replaced by [*], counts shown as ×N)"
              + (f"; {dropped} groups did not fit and are not listed" if dropped else "")
              + ". Judge the plan from addresses and actions only, and say in your summary that the diff "
              "was not reviewed.")
    return (f"{notice}\n\n{totals[0] if totals else ''}\n" + "\n".join(shown))


def build_plan_section(plan_text, limit=MAX_PLAN_CHARS):
    """The plan as the model sees it: the whole noise-free plan when it fits,
    otherwise a complete change list, the diff cut at a resource boundary,
    and an exact statement of what was left out."""
    clean = strip_refresh_noise(plan_text)
    if limit is None or len(clean) <= limit:
        changes = resource_changes(clean)
        totals = [line.strip() for line in clean.splitlines() if line.startswith("Plan:")]
        return ("COMPLETE PLAN: the output below is the entire `terraform plan` output, with only "
                "state-refresh / data-source read lines removed. Nothing was cut: it contains all "
                f"{len(changes)} resource change headers"
                + (f" and Terraform's own summary line ({totals[0]})" if totals else "")
                + ". Review every change in it, and do not say the plan is truncated, partial or "
                "incomplete.\n\n"
                f"Terraform plan output (complete):\n```\n{clean}\n```")

    lines = clean.splitlines()
    changes = resource_changes(clean)
    totals = [line.strip() for line in lines if line.startswith("Plan:")]
    omitted_mark = " [diff not shown]"

    # Budget for the diff: what's left after the change list (sized as if
    # every entry carried the omitted marker) and ~1k chars of notice.
    list_size = sum(len(f"- {addr}: {action}{omitted_mark}\n") for _, addr, action in changes)
    if list_size > limit // 2:
        return _grouped_section(clean, changes, totals, limit)
    budget = max(limit - list_size - 1_000, 0)
    used, cut = 0, 0
    for i, line in enumerate(lines):
        if used + len(line) + 1 > budget:
            break
        used += len(line) + 1
        cut = i + 1
    # Back off to the last resource header so no resource's diff is shown half-way.
    boundary = max((i for i, _, _ in changes if i <= cut), default=0)
    if boundary > 0:
        cut = boundary
    omitted = sum(1 for i, _, _ in changes if i >= cut)
    change_list = "\n".join(f"- {addr}: {action}{omitted_mark if i >= cut else ''}"
                             for i, addr, action in changes) or "- (no resource change headers found)"

    notice = (f"NOTE: this plan is {len(clean):,} characters (after removing refresh lines), more than the "
              f"{limit:,}-character review limit. The list below names every one of the {len(changes)} "
              f"resource changes, but the diff after it covers only the first {len(changes) - omitted} "
              f"and stops at line {cut} of {len(lines)}. The diff of the {omitted} changes marked "
              f"'{omitted_mark.strip()}' is not shown, nor any plan output after them (warnings, outputs). "
              "Judge those from their address and action only, and say in your summary that part of the "
              "diff was not reviewed.")
    return (f"{notice}\n\nAll {len(changes)} resource changes"
            f"{' (' + totals[0] + ')' if totals else ''}:\n{change_list}\n\n"
            f"Terraform plan output (partial, first {cut} lines):\n```\n" + "\n".join(lines[:cut]) + "\n```")


def review_plan(plan_text, converse=cli_converse):
    """Send plan to Bedrock for LLM review, return structured dict (or None)."""
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

Reading the diff: Terraform itself elides parts of every plan by design. These
markers are normal Terraform output, not truncation; on their own they are
never a reason to say the plan or the review is incomplete:
- "# (N unchanged attributes hidden)", "# (N unchanged blocks hidden)",
  "# (N unchanged elements hidden)": for an update, Terraform shows only the
  changed attributes. The hidden ones are not changing.
- "(sensitive value)": the value is redacted on purpose.
- "(known after apply)": the value is computed by AWS during apply.
- jsonencode(...) policy diffs show only the changed statements and fields, so
  a changed statement can appear without its Effect, Resource or Sid.
In a changed IAM policy statement an unchanged `Effect` is usually not shown, so
do not assume an added action is an Allow - it may be a Deny. A Sid such as
"Deny*" or "Block*" is a strong hint the statement denies. Only call out a
permission grant when the plan shows `Effect = "Allow"` or the statement is new.

{build_plan_section(plan_text)}"""

    messages = [{"role": "user", "content": [{"text": prompt}]}]

    response = converse(messages, REVIEW_TOOL_CONFIG)
    if not response:
        return None
    if response.get("stopReason") == "max_tokens":
        # A cut-off tool call may parse but be missing findings; don't trust it.
        print("Review stopped at the output token limit; discarding the partial answer.", file=sys.stderr)
        return None

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
