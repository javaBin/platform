#!/usr/bin/env python3
"""Resolve group memberships from groups.yaml + members.yaml + access.yaml and invoke the team-provisioner Lambda.

Usage: provision-groups.py <groups.yaml> <members.yaml> [access.yaml] [--prev-members PATH] [--prev-groups PATH] [--prev-access PATH]

Reads all YAML files, resolves which members belong to which groups based on
the memberships field in members.yaml, and invokes the javabin-team-provisioner
Lambda with action=sync_groups_and_heros.

When --prev-* flags are provided, detects removals by comparing current vs
previous YAML. Posts removal warnings to Slack if SLACK_WEBHOOK_URL is set.

Requires: aws CLI, PyYAML (available on GitHub Actions ubuntu-latest runners)
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
import urllib.request

import yaml


def load_yaml(path):
    if not path or not os.path.exists(path):
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def resolve_memberships(groups, members_data):
    """For each group, determine which members belong to it."""
    members_list = members_data.get("members") or []

    resolved_groups = []
    for group in groups.get("groups", []):
        name = group["name"]

        member_emails = [
            h["javabin_google_email"]
            for h in members_list
            if h.get("javabin_google_email") and name in (h.get("memberships") or [])
        ]

        resolved = dict(group)
        resolved["members"] = member_emails
        resolved_groups.append(resolved)

    return resolved_groups


def build_member_details(members_data):
    """Extract member details needed for account creation and alias management."""
    members_list = members_data.get("members") or []
    return [
        {
            "javabin_google_email": h["javabin_google_email"],
            "firstname": h["firstname"],
            "lastname": h["lastname"],
            "personal_email": h["personal_email"],
            "alias": h.get("alias") or "",
        }
        for h in members_list
        if h.get("javabin_google_email")
    ]


def invoke_lambda(payload):
    """Invoke the team-provisioner Lambda and print the response."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False) as f:
        json.dump(payload, f)
        payload_file = f.name

    result = subprocess.run(
        [
            "aws", "lambda", "invoke",
            "--function-name", "javabin-team-provisioner",
            "--payload", f"fileb://{payload_file}",
            "--cli-binary-format", "raw-in-base64-out",
            # Lambda timeout is 300s; default CLI read timeout (60s) is too low.
            # Disable client-side read timeout so the CLI waits for the Lambda response.
            "--cli-read-timeout", "0",
            "/tmp/lambda-response.json",
        ],
        capture_output=True,
        text=True,
    )

    if result.returncode != 0:
        print(f"Lambda invocation failed: {result.stderr}", file=sys.stderr)
        sys.exit(1)

    with open("/tmp/lambda-response.json") as f:
        print(f.read())


def detect_removals(prev_members, curr_members, prev_groups, curr_groups):
    """Detect members removed from YAML and group membership removals."""
    prev_by_email = {m["personal_email"].lower(): m for m in (prev_members.get("members") or []) if m.get("personal_email")}
    curr_by_email = {m["personal_email"].lower(): m for m in (curr_members.get("members") or []) if m.get("personal_email")}

    removed_members = [prev_by_email[e] for e in sorted(prev_by_email.keys() - curr_by_email.keys())]

    membership_removals = []
    all_group_names = {g["name"] for g in (prev_groups.get("groups") or [])} | {g["name"] for g in (curr_groups.get("groups") or [])}
    for gname in sorted(all_group_names):
        prev_set = {
            m["javabin_google_email"] for m in (prev_members.get("members") or [])
            if m.get("javabin_google_email") and gname in (m.get("memberships") or [])
        }
        curr_set = {
            m["javabin_google_email"] for m in (curr_members.get("members") or [])
            if m.get("javabin_google_email") and gname in (m.get("memberships") or [])
        }
        removed = sorted(prev_set - curr_set)
        if removed:
            membership_removals.append({"group": gname, "removed": removed})

    removed_groups = [
        g["name"] for g in (prev_groups.get("groups") or [])
        if g["name"] not in {g2["name"] for g2 in (curr_groups.get("groups") or [])}
    ]

    return removed_members, membership_removals, removed_groups


def post_removal_warning(removed_members, membership_removals, removed_groups):
    """Post a removal warning to Slack."""
    webhook_url = os.environ.get("SLACK_WEBHOOK_URL")
    if not webhook_url:
        return

    lines = [":warning: *Removal Detected* (additive-only — no auto-action taken)\n"]

    if removed_members:
        lines.append("*Members removed from YAML:*")
        for m in removed_members:
            name = f"{m.get('firstname', '')} {m.get('lastname', '')}"
            groups = ", ".join(m.get("memberships") or [])
            lines.append(f"• {name} ({m.get('javabin_google_email', '')}) — was in: {groups}")
        lines.append("")

    if membership_removals:
        lines.append("*Group membership removals:*")
        for mr in membership_removals:
            for email in mr["removed"]:
                lines.append(f"• {email} removed from {mr['group']}")
        lines.append("")

    if removed_groups:
        lines.append("*Groups removed:*")
        for g in removed_groups:
            lines.append(f"• {g}")
        lines.append("")

    lines.append("Run the `reconcile` workflow to apply these removals.")

    payload = json.dumps({"text": "\n".join(lines)}).encode()
    req = urllib.request.Request(webhook_url, data=payload, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req)
        print("Posted removal warning to Slack")
    except Exception as e:
        print(f"Warning: Slack notification failed: {e}", file=sys.stderr)


def main():
    parser = argparse.ArgumentParser(description="Provision groups and members via Lambda")
    parser.add_argument("groups_yaml", help="Path to groups.yaml")
    parser.add_argument("members_yaml", help="Path to members.yaml")
    parser.add_argument("access_yaml", nargs="?", help="Path to access.yaml")
    parser.add_argument("--prev-members", help="Previous members.yaml for removal detection")
    parser.add_argument("--prev-groups", help="Previous groups.yaml for removal detection")
    parser.add_argument("--prev-access", help="Previous access.yaml for removal detection")
    args = parser.parse_args()

    groups = load_yaml(args.groups_yaml)
    members_data = load_yaml(args.members_yaml)
    access_groups = load_yaml(args.access_yaml).get("access_groups", []) if args.access_yaml else []

    resolved_groups = resolve_memberships(groups, members_data)
    member_details = build_member_details(members_data)

    total_members = sum(len(g["members"]) for g in resolved_groups)
    print(f"Resolved {len(resolved_groups)} groups with {total_members} total memberships")
    for g in resolved_groups:
        print(f"  {g['name']}: {len(g['members'])} member(s)")

    if access_groups:
        print(f"\nAccess groups: {len(access_groups)}")
        for ag in access_groups:
            print(f"  {ag['name']}: {', '.join(ag.get('groups', []))}")

    # Detect removals if previous state is provided
    if args.prev_members:
        prev_members = load_yaml(args.prev_members)
        prev_groups = load_yaml(args.prev_groups) if args.prev_groups else groups
        removed_members, membership_removals, removed_groups = detect_removals(
            prev_members, members_data, prev_groups, groups,
        )
        if removed_members or membership_removals or removed_groups:
            print(f"\nRemovals detected:")
            if removed_members:
                print(f"  {len(removed_members)} member(s) removed from YAML")
            if membership_removals:
                total_rm = sum(len(mr["removed"]) for mr in membership_removals)
                print(f"  {total_rm} group membership removal(s)")
            if removed_groups:
                print(f"  {len(removed_groups)} group(s) removed")
            post_removal_warning(removed_members, membership_removals, removed_groups)
        else:
            print("\nNo removals detected")

    # Pass CI context so the Lambda can attribute the sync in Slack
    ci_context = {
        k: v for k, v in {
            "actor": os.environ.get("GITHUB_ACTOR"),
            "repo": os.environ.get("GITHUB_REPOSITORY"),
            "sha": os.environ.get("GITHUB_SHA"),
            "run_id": os.environ.get("GITHUB_RUN_ID"),
        }.items() if v
    }

    payload = {
        "action": "sync_groups_and_heros",
        "groups": resolved_groups,
        "heros": member_details,
        **({"access_groups": access_groups} if access_groups else {}),
        **({"ci_context": ci_context} if ci_context else {}),
    }

    print(f"\nInvoking javabin-team-provisioner with {len(member_details)} member(s)...")
    invoke_lambda(payload)


if __name__ == "__main__":
    main()
