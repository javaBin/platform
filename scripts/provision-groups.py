#!/usr/bin/env python3
"""Resolve group memberships from groups.yaml + heros.yaml and invoke the team-provisioner Lambda.

Usage: provision-groups.py <groups.yaml> <heros.yaml>

Reads both YAML files, resolves which heroes belong to which groups based on
the memberships field in heros.yaml, and invokes the javabin-team-provisioner
Lambda with action=sync_groups_and_heros.

All group memberships are explicit — heroes must list each group in their memberships field.

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


def resolve_memberships(groups, heros):
    """For each group, determine which heroes are members."""
    members_list = heros.get("members") or []

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


def build_hero_details(heros):
    """Extract hero details needed for account creation and alias management."""
    members_list = heros.get("members") or []
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
    if len(sys.argv) != 3:
        print(f"Usage: {sys.argv[0]} <groups.yaml> <heros.yaml>", file=sys.stderr)
        sys.exit(1)

    groups_path, heros_path = sys.argv[1], sys.argv[2]

    groups = load_yaml(groups_path)
    heros = load_yaml(heros_path)

    resolved_groups = resolve_memberships(groups, heros)
    hero_details = build_hero_details(heros)

    total_members = sum(len(g["members"]) for g in resolved_groups)
    print(f"Resolved {len(resolved_groups)} groups with {total_members} total memberships")
    for g in resolved_groups:
        print(f"  {g['name']}: {len(g['members'])} member(s)")

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
        "heros": hero_details,
        **({"ci_context": ci_context} if ci_context else {}),
    }

    print(f"\nInvoking javabin-team-provisioner with {len(hero_details)} hero(es)...")
    invoke_lambda(payload)


if __name__ == "__main__":
    main()
