#!/usr/bin/env python3
"""Resolve group memberships from groups.yaml + members.yaml + access.yaml and invoke the team-provisioner Lambda.

Usage: provision-groups.py <groups.yaml> <members.yaml> <access.yaml>

Reads all YAML files, resolves which members belong to which groups based on
the memberships field in members.yaml, and invokes the javabin-team-provisioner
Lambda with action=sync_groups_and_heros.

Access groups from access.yaml define which role groups get Google Workspace
service access (email, drive, chat, meet). The Lambda syncs these as nested
group memberships in Google Workspace.

Requires: aws CLI, PyYAML (available on GitHub Actions ubuntu-latest runners)
"""

import json
import os
import subprocess
import sys
import tempfile

import yaml


def load_yaml(path):
    with open(path) as f:
        return yaml.safe_load(f)


def resolve_memberships(groups, members_data):
    """For each group, determine which members belong to it."""
    members_list = members_data.get("members") or []

    resolved_groups = []
    for group in groups.get("groups", []):
        name = group["name"]

        # All groups use explicit memberships — no implicit membership
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
            "type": h.get("type", "helt"),
            "alias": h.get("alias") or "",
        }
        for h in members_list
        if h.get("javabin_google_email")
    ]


def load_access_groups(access_path):
    """Load access groups from access.yaml."""
    if not access_path or not os.path.exists(access_path):
        return []
    data = load_yaml(access_path)
    return data.get("access_groups", [])


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


def main():
    if len(sys.argv) < 3:
        print(f"Usage: {sys.argv[0]} <groups.yaml> <members.yaml> [access.yaml]", file=sys.stderr)
        sys.exit(1)

    groups_path = sys.argv[1]
    members_path = sys.argv[2]
    access_path = sys.argv[3] if len(sys.argv) > 3 else None

    groups = load_yaml(groups_path)
    members_data = load_yaml(members_path)
    access_groups = load_access_groups(access_path)

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
