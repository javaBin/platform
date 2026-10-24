#!/usr/bin/env python3
"""Resolve groups/atlassian.yaml to java.no emails and invoke the team-provisioner Lambda.

Usage: provision-atlassian.py <atlassian.yaml> <members.yaml>

Each Atlassian group in atlassian.yaml lists the registry groups (`from`) whose
members belong in it. Members are resolved to their javabin_google_email — the
account the Google Workspace user sync creates in Atlassian — so personal
emails never reach Atlassian. The Lambda runs with action=sync_atlassian and
reports (or, when enabled in Terraform, applies) the membership changes.

Requires: aws CLI, PyYAML (available on GitHub Actions ubuntu-latest runners)
"""

import argparse
import importlib.util
import os
import sys


def _provision_groups():
    """Load provision-groups.py for its YAML + Lambda helpers (needs PyYAML, so only at run time)."""
    spec = importlib.util.spec_from_file_location(
        "provision_groups", os.path.join(os.path.dirname(os.path.abspath(__file__)), "provision-groups.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def resolve_atlassian_groups(atlassian_data, members_data):
    """For each Atlassian group, collect the java.no emails of members in its source groups."""
    members = members_data.get("members") or []
    resolved = []
    for group in atlassian_data.get("groups") or []:
        sources = set(group.get("from") or [])
        emails = sorted({
            m["javabin_google_email"].lower()
            for m in members
            if m.get("javabin_google_email") and sources & set(m.get("memberships") or [])
        })
        resolved.append({
            "name": group["name"],
            "description": group.get("description") or "",
            "members": emails,
        })
    return resolved


def main():
    parser = argparse.ArgumentParser(description="Sync Atlassian groups from the registry via Lambda")
    parser.add_argument("atlassian_yaml", help="Path to groups/atlassian.yaml")
    parser.add_argument("members_yaml", help="Path to groups/members.yaml")
    args = parser.parse_args()
    provision_groups = _provision_groups()

    atlassian_data = provision_groups.load_yaml(args.atlassian_yaml)
    if not atlassian_data.get("groups"):
        print(f"No groups in {args.atlassian_yaml} — nothing to sync")
        return

    groups = resolve_atlassian_groups(atlassian_data, provision_groups.load_yaml(args.members_yaml))
    for g in groups:
        print(f"  {g['name']}: {len(g['members'])} member(s)")

    ci_context = {
        k: v for k, v in {
            "actor": os.environ.get("GITHUB_ACTOR"),
            "repo": os.environ.get("GITHUB_REPOSITORY"),
            "sha": os.environ.get("GITHUB_SHA"),
            "run_id": os.environ.get("GITHUB_RUN_ID"),
        }.items() if v
    }
    payload = {
        "action": "sync_atlassian",
        "groups": groups,
        **({"ci_context": ci_context} if ci_context else {}),
    }

    print(f"\nInvoking javabin-team-provisioner (sync_atlassian) for {len(groups)} group(s)...")
    provision_groups.invoke_lambda(payload)


if __name__ == "__main__":
    sys.exit(main())
