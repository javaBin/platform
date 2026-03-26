#!/usr/bin/env python3
"""Diff groups/ YAML files between base and PR branch, output a Markdown plan comment.

Compares members.yaml, groups.yaml, and access.yaml to show what will change
when the PR is merged. Pure YAML diffing — no AWS or Google credentials needed.

Usage:
  diff-groups.py --base-members BASE --pr-members PR \
                 --base-groups BASE --pr-groups PR \
                 --base-access BASE --pr-access PR

Output: Markdown to stdout, suitable for a GitHub PR comment.
"""

import argparse
import sys

import yaml


def load_yaml(path):
    try:
        with open(path) as f:
            return yaml.safe_load(f) or {}
    except FileNotFoundError:
        return {}


def diff_members(base_data, pr_data):
    base_members = {m["personal_email"].lower(): m for m in (base_data.get("members") or []) if m.get("personal_email")}
    pr_members = {m["personal_email"].lower(): m for m in (pr_data.get("members") or []) if m.get("personal_email")}

    added = [pr_members[e] for e in sorted(pr_members.keys() - base_members.keys())]
    removed = [base_members[e] for e in sorted(base_members.keys() - pr_members.keys())]

    changed = []
    for email in sorted(base_members.keys() & pr_members.keys()):
        b, p = base_members[email], pr_members[email]
        diffs = []
        for field in ["firstname", "lastname", "javabin_google_email", "alias"]:
            bv, pv = b.get(field, ""), p.get(field, "")
            if bv != pv:
                diffs.append(f"`{field}`: {bv!r} → {pv!r}")
        b_groups = set(b.get("memberships") or [])
        p_groups = set(p.get("memberships") or [])
        if b_groups != p_groups:
            added_g = p_groups - b_groups
            removed_g = b_groups - p_groups
            parts = []
            if added_g:
                parts.append(f"+{', '.join(sorted(added_g))}")
            if removed_g:
                parts.append(f"-{', '.join(sorted(removed_g))}")
            diffs.append(f"`memberships`: {' '.join(parts)}")
        if diffs:
            changed.append({"member": p, "diffs": diffs})

    return added, removed, changed


def diff_groups(base_data, pr_data):
    base_groups = {g["name"]: g for g in (base_data.get("groups") or [])}
    pr_groups = {g["name"]: g for g in (pr_data.get("groups") or [])}

    added = [pr_groups[n] for n in sorted(pr_groups.keys() - base_groups.keys())]
    removed = [base_groups[n] for n in sorted(base_groups.keys() - pr_groups.keys())]

    changed = []
    for name in sorted(base_groups.keys() & pr_groups.keys()):
        b, p = base_groups[name], pr_groups[name]
        diffs = []
        for field in ["google", "cognito", "identity_center", "permission_set"]:
            bv, pv = b.get(field), p.get(field)
            if bv != pv:
                diffs.append(f"`{field}`: {bv!r} → {pv!r}")
        if diffs:
            changed.append({"name": name, "diffs": diffs})

    return added, removed, changed


def diff_access(base_data, pr_data):
    base_ag = {a["name"]: set(a.get("groups", [])) for a in (base_data.get("access_groups") or [])}
    pr_ag = {a["name"]: set(a.get("groups", [])) for a in (pr_data.get("access_groups") or [])}

    lines = []
    all_names = sorted(set(base_ag) | set(pr_ag))
    for name in all_names:
        bg = base_ag.get(name, set())
        pg = pr_ag.get(name, set())
        if bg != pg:
            added = pg - bg
            removed = bg - pg
            parts = []
            if added:
                parts.append(f"+{', '.join(sorted(added))}")
            if removed:
                parts.append(f"-{', '.join(sorted(removed))}")
            if name not in base_ag:
                parts = ["new"]
            elif name not in pr_ag:
                parts = ["removed"]
            lines.append(f"- **{name}**: {' '.join(parts)}")
    return lines


def resolve_membership_changes(base_members_data, pr_members_data, base_groups_data, pr_groups_data):
    """Show per-group membership additions/removals."""
    all_group_names = sorted(
        {g["name"] for g in (base_groups_data.get("groups") or [])}
        | {g["name"] for g in (pr_groups_data.get("groups") or [])}
    )

    base_members = base_members_data.get("members") or []
    pr_members = pr_members_data.get("members") or []

    def members_of(members_list, group_name):
        return {
            m["javabin_google_email"]
            for m in members_list
            if m.get("javabin_google_email") and group_name in (m.get("memberships") or [])
        }

    lines = []
    for gname in all_group_names:
        base_set = members_of(base_members, gname)
        pr_set = members_of(pr_members, gname)
        added = sorted(pr_set - base_set)
        removed = sorted(base_set - pr_set)
        if added or removed:
            parts = []
            for e in added:
                parts.append(f"  - +{e}")
            for e in removed:
                parts.append(f"  - -{e} *(additive-only — will not auto-remove)*")
            lines.append(f"- **{gname}**:")
            lines.extend(parts)
    return lines


def format_markdown(added, removed, changed, group_added, group_removed, group_changed,
                    access_lines, membership_lines):
    lines = ["<!-- groups-plan -->", "## Provisioning Plan", ""]

    has_content = False

    if added:
        has_content = True
        lines.append(f"### New Members ({len(added)})")
        lines.append("| Name | Email | Groups |")
        lines.append("|------|-------|--------|")
        for m in added:
            name = f"{m.get('firstname', '')} {m.get('lastname', '')}"
            groups = ", ".join(m.get("memberships") or [])
            lines.append(f"| {name} | {m.get('javabin_google_email', '')} | {groups} |")
        lines.append("")

    if removed:
        has_content = True
        lines.append(f"### Removed Members ({len(removed)}) — manual reconcile required")
        lines.append("| Name | Email | Groups |")
        lines.append("|------|-------|--------|")
        for m in removed:
            name = f"{m.get('firstname', '')} {m.get('lastname', '')}"
            groups = ", ".join(m.get("memberships") or [])
            lines.append(f"| {name} | {m.get('javabin_google_email', '')} | {groups} |")
        lines.append("")

    if changed:
        has_content = True
        lines.append(f"### Changed Members ({len(changed)})")
        for c in changed:
            m = c["member"]
            name = f"{m.get('firstname', '')} {m.get('lastname', '')}"
            lines.append(f"- **{name}** ({m.get('javabin_google_email', '')})")
            for d in c["diffs"]:
                lines.append(f"  - {d}")
        lines.append("")

    if group_added or group_removed or group_changed:
        has_content = True
        lines.append("### Group Definition Changes")
        for g in group_added:
            lines.append(f"- **{g['name']}**: new group ({g.get('google', '')})")
        for g in group_removed:
            lines.append(f"- **{g['name']}**: removed")
        for g in group_changed:
            lines.append(f"- **{g['name']}**: {', '.join(g['diffs'])}")
        lines.append("")

    if access_lines:
        has_content = True
        lines.append("### Access Group Changes")
        lines.extend(access_lines)
        lines.append("")

    if membership_lines:
        has_content = True
        lines.append("### Group Membership Changes")
        lines.extend(membership_lines)
        lines.append("")

    if not has_content:
        lines.append("No provisioning changes detected.")
        lines.append("")

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description="Diff groups/ YAML for PR plan comment")
    parser.add_argument("--base-members", required=True)
    parser.add_argument("--pr-members", required=True)
    parser.add_argument("--base-groups", required=True)
    parser.add_argument("--pr-groups", required=True)
    parser.add_argument("--base-access", required=True)
    parser.add_argument("--pr-access", required=True)
    args = parser.parse_args()

    base_members = load_yaml(args.base_members)
    pr_members = load_yaml(args.pr_members)
    base_groups = load_yaml(args.base_groups)
    pr_groups = load_yaml(args.pr_groups)
    base_access = load_yaml(args.base_access)
    pr_access = load_yaml(args.pr_access)

    added, removed, changed = diff_members(base_members, pr_members)
    group_added, group_removed, group_changed = diff_groups(base_groups, pr_groups)
    access_lines = diff_access(base_access, pr_access)
    membership_lines = resolve_membership_changes(base_members, pr_members, base_groups, pr_groups)

    md = format_markdown(added, removed, changed, group_added, group_removed, group_changed,
                         access_lines, membership_lines)
    print(md)


if __name__ == "__main__":
    main()
