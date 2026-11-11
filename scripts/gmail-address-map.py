#!/usr/bin/env python3
"""Print the Gmail recipient address map for members who opt in to forwarding.

Usage: gmail-address-map.py <members.yaml>

Mail to a java.no account stays in its java.no inbox. A member with
`forward_to_personal: true` in the registry's groups/members.yaml also gets it
at their `personal_email`, through a Gmail routing rule: Admin console → Apps →
Google Workspace → Gmail → Routing → "Personlige aliaser" (Email forwarding
using recipient address map), with "Also route to original destination" ON.
The rule has no API, so an admin replaces its address mappings with this output
whenever the opted-in members change, and disables the rule when there are none.
(Forwarding through the Gmail API needs the recipient to confirm every address.)

Output: one "java.no address, personal address" line per opted-in member, sorted.

Requires: PyYAML
"""

import argparse
import sys


def address_map(members_data):
    """Sorted (java.no email, personal email) pairs for members with forward_to_personal: true."""
    pairs = {
        m["javabin_google_email"].strip().lower(): m["personal_email"].strip()
        for m in members_data.get("members") or []
        if m.get("forward_to_personal") is True
        and (m.get("javabin_google_email") or "").strip()
        and (m.get("personal_email") or "").strip()
    }
    return sorted(pairs.items())


def main():
    import yaml

    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("members_yaml", help="Path to groups/members.yaml")
    args = parser.parse_args()
    with open(args.members_yaml) as f:
        pairs = address_map(yaml.safe_load(f))
    if not pairs:
        print("No member has forward_to_personal: true. Disable the routing rule.", file=sys.stderr)
    for java_no, personal in pairs:
        sys.stdout.write(f"{java_no}, {personal}\n")


if __name__ == "__main__":
    main()
