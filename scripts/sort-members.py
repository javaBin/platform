#!/usr/bin/env python3
"""Sort members.yaml by highest-priority group membership.

Groups members into sections by their highest-priority group, adds section
comment headers, and sorts within each section by lastname.

Usage:
  sort-members.py groups/members.yaml groups/groups.yaml [--in-place]
  sort-members.py groups/members.yaml groups/groups.yaml > sorted.yaml

Priority order (highest first):
  drift → styret → pkom → kodesmia → region → javazone → other
"""

import argparse
import sys
from collections import defaultdict

import yaml

GROUP_PRIORITY = ["drift", "styret", "pkom", "kodesmia", "region", "javazone"]


def primary_section(member):
    """Determine which section a member belongs to based on highest-priority group."""
    memberships = member.get("memberships") or []
    for group in GROUP_PRIORITY:
        if group in memberships:
            return group
    return "other"


def sort_members(members):
    """Group and sort members by priority section, then by lastname."""
    sections = defaultdict(list)
    for m in members:
        sections[primary_section(m)].append(m)

    ordered = []
    for group in GROUP_PRIORITY + ["other"]:
        if group not in sections:
            continue
        section_members = sorted(
            sections[group],
            key=lambda m: (m.get("lastname", "").lower(), m.get("firstname", "").lower()),
        )
        ordered.append((group, section_members))
    return ordered


def format_yaml(sections):
    """Format sorted members into YAML with section comments."""
    lines = [
        "# Member registry — source of truth for all javaBin members with java.no accounts.",
        "#",
        "# Synced from the yearly Google Forms hero application via sync-members workflow.",
        "# Board approves applications in the Google Sheet, then triggers a sync that",
        "# creates a PR updating this file.",
        "#",
        "# Rules:",
        "#   - All group memberships are explicit (including helter)",
        "#   - memberships references group names from groups.yaml",
        "#   - alias is optional — members can PR their preferred alias",
        "#   - javabin_google_email is the Google Workspace account (firstname.lastname@java.no)",
        "#   - personal_email is the unique key for dedup/merge during sync",
        "",
    ]

    if not sections:
        lines.append("members: []")
    else:
        lines.append("members:")
        first_section = True
        for section_name, members in sections:
            if not first_section:
                lines.append("")
            lines.append(f"  # {section_name}")
            first_section = False
            for m in members:
                lines.append(f"  - firstname: {m['firstname']}")
                lines.append(f"    lastname: {m['lastname']}")
                lines.append(f"    personal_email: {m['personal_email']}")
                lines.append(f"    javabin_google_email: {m['javabin_google_email']}")
                alias = m.get("alias") or ""
                lines.append(f"    alias: {alias}" if alias else "    alias:")
                memberships = m.get("memberships", [])
                if memberships:
                    lines.append(f"    memberships: [{', '.join(memberships)}]")
                else:
                    lines.append("    memberships: []")
                lines.append("")

    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description="Sort members.yaml by group priority")
    parser.add_argument("members_yaml", help="Path to members.yaml")
    parser.add_argument("groups_yaml", help="Path to groups.yaml (for validation)")
    parser.add_argument("--in-place", action="store_true", help="Overwrite the file")
    args = parser.parse_args()

    with open(args.members_yaml) as f:
        data = yaml.safe_load(f) or {}

    members = data.get("members") or []
    if not members:
        print("No members to sort", file=sys.stderr)
        return

    sections = sort_members(members)
    output = format_yaml(sections)

    if args.in_place:
        with open(args.members_yaml, "w") as f:
            f.write(output)
        total = sum(len(s) for _, s in sections)
        print(f"Sorted {total} members into {len(sections)} sections", file=sys.stderr)
    else:
        print(output, end="")


if __name__ == "__main__":
    main()
