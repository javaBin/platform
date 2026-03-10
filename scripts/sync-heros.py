#!/usr/bin/env python3
"""Sync approved heroes from a Google Sheet into heros.yaml.

Reads the hero applications spreadsheet via the Google Sheets API,
filters for approved entries, normalizes names and emails, and outputs
an updated heros.yaml that merges new entries with existing ones.

Usage:
  sync-heros.py --sheet-id ID --range RANGE --sa-json PATH --admin-email EMAIL \
                 [--existing PATH] [--output PATH]

The column and group mappings are configurable dicts at the top of the file.
When the form structure changes, update COLUMN_MAP and GROUP_MAP.

Requires: No external dependencies (stdlib only).
"""

import argparse
import base64
import json
import os
import subprocess
import sys
import tempfile
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request


# ---------------------------------------------------------------------------
# Configurable mappings — update these when the form changes
# ---------------------------------------------------------------------------

# Maps internal field names to Google Sheet column headers.
COLUMN_MAP = {
    "name": "Navn",
    "email": "Epostadresse",
    "groups": "Gruppetilhørlighet",
    "approved": "Approved",
}

# Maps Norwegian group names from the sheet to group IDs in groups.yaml.
GROUP_MAP = {
    "JavaZone": "javazone",
    "Styret": "styret",
    "Kodesmia": "kodesmia",
    "Region": "region",
    "PKOM": "pkom",
}


# ---------------------------------------------------------------------------
# Google Sheets API auth (JWT + domain-wide delegation, same pattern as handler.py)
# ---------------------------------------------------------------------------

SHEETS_SCOPE = "https://www.googleapis.com/auth/spreadsheets.readonly"


def _b64url(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign_rs256(message_bytes, private_key_pem):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=True) as key_file:
        key_file.write(private_key_pem)
        key_file.flush()
        result = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", key_file.name],
            input=message_bytes,
            capture_output=True,
        )
    if result.returncode != 0:
        raise RuntimeError(f"openssl signing failed: {result.stderr.decode()}")
    return result.stdout


def _create_jwt(payload, private_key_pem):
    header = _b64url(json.dumps({"alg": "RS256", "typ": "JWT"}))
    body = _b64url(json.dumps(payload))
    signing_input = f"{header}.{body}".encode("ascii")
    signature = _b64url(_sign_rs256(signing_input, private_key_pem))
    return f"{header}.{body}.{signature}"


def get_sheets_access_token(sa_json_path, admin_email):
    """Exchange a domain-wide delegation JWT for a Google OAuth2 access token."""
    with open(sa_json_path) as f:
        sa = json.load(f)

    now = int(time.time())
    signed_jwt = _create_jwt(
        {
            "iss": sa["client_email"],
            "sub": admin_email,
            "scope": SHEETS_SCOPE,
            "aud": "https://oauth2.googleapis.com/token",
            "iat": now,
            "exp": now + 3600,
        },
        sa["private_key"],
    )

    data = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": signed_jwt,
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://oauth2.googleapis.com/token",
        data=data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    with urllib.request.urlopen(req) as resp:
        return json.loads(resp.read())["access_token"]


# ---------------------------------------------------------------------------
# Sheet reading
# ---------------------------------------------------------------------------

def read_sheet(access_token, sheet_id, sheet_range):
    """Read rows from a Google Sheet. Returns list of dicts keyed by header."""
    encoded_range = urllib.parse.quote(sheet_range)
    url = f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{encoded_range}"
    req = urllib.request.Request(url)
    req.add_header("Authorization", f"Bearer {access_token}")

    with urllib.request.urlopen(req) as resp:
        data = json.loads(resp.read())

    rows = data.get("values", [])
    if len(rows) < 2:
        return []

    headers = rows[0]
    result = []
    for row in rows[1:]:
        entry = {}
        for i, header in enumerate(headers):
            entry[header] = row[i].strip() if i < len(row) else ""
        result.append(entry)
    return result


# ---------------------------------------------------------------------------
# Name and email normalization
# ---------------------------------------------------------------------------

def strip_diacritics(s):
    """Strip diacritics for email generation. ø→o, æ→ae, å→a, etc."""
    # Handle specific Norwegian chars before general normalization
    replacements = {"ø": "o", "Ø": "O", "æ": "ae", "Æ": "AE", "å": "a", "Å": "A"}
    for old, new in replacements.items():
        s = s.replace(old, new)
    # General diacritics removal (handles accented chars like é, ü, etc.)
    normalized = unicodedata.normalize("NFKD", s)
    return "".join(c for c in normalized if not unicodedata.combining(c))


def parse_name(full_name):
    """Split full name into (firstname, lastname). Last token = lastname, rest = firstname."""
    parts = full_name.strip().split()
    if len(parts) == 0:
        return "", ""
    if len(parts) == 1:
        return parts[0], parts[0]
    return " ".join(parts[:-1]), parts[-1]


def derive_email(firstname, lastname):
    """Derive javabin_google_email from name parts. Lowercase, dot-separated, diacritics stripped."""
    parts = firstname.split() + [lastname]
    normalized = [strip_diacritics(p).lower() for p in parts if p]
    return ".".join(normalized) + "@java.no"


def map_groups(groups_str):
    """Map comma-separated Norwegian group names to group IDs."""
    if not groups_str:
        return []
    raw = [g.strip() for g in groups_str.split(",") if g.strip()]
    mapped = []
    for g in raw:
        group_id = GROUP_MAP.get(g)
        if group_id:
            mapped.append(group_id)
        else:
            print(f"  Warning: unknown group '{g}' — not in GROUP_MAP, skipping", file=sys.stderr)
    return sorted(set(mapped))


# ---------------------------------------------------------------------------
# YAML output (stdlib — no yaml dependency needed for writing)
# ---------------------------------------------------------------------------

def format_yaml(members):
    """Format the heros.yaml content. Uses simple string formatting to avoid yaml dependency."""
    lines = [
        "# Hero membership registry — source of truth for all javaBin hero volunteers.",
        "#",
        "# Synced from the yearly Google Forms hero application via sync-heros workflow.",
        "# Board approves applications in the Google Sheet, then triggers a sync that",
        "# creates a PR updating this file.",
        "#",
        "# Rules:",
        "#   - All members are implicitly in the 'helter' group (not listed in memberships)",
        "#   - memberships references group names from groups.yaml",
        "#   - alias is optional — heroes can PR their preferred alias",
        "#   - javabin_google_email is the Google Workspace account (firstname.lastname@java.no)",
        "#   - personal_email is the unique key for dedup/merge during sync",
        "",
    ]

    if not members:
        lines.append("members: []")
    else:
        lines.append("members:")
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


# ---------------------------------------------------------------------------
# Merge logic
# ---------------------------------------------------------------------------

def load_existing(path):
    """Load existing heros.yaml. Returns dict keyed by personal_email (lowercase)."""
    if not path or not os.path.exists(path):
        return {}
    try:
        # Use yaml if available, fall back to simple parsing
        import yaml
        with open(path) as f:
            data = yaml.safe_load(f)
        if not data or "members" not in data:
            return {}
        return {m["personal_email"].lower(): m for m in data["members"] if m.get("personal_email")}
    except ImportError:
        # Fallback: return empty, sync will create fresh
        print("Warning: PyYAML not available, cannot merge with existing heros.yaml", file=sys.stderr)
        return {}


def merge_heroes(existing, new_entries):
    """Merge new entries into existing. Preserves alias from existing entries."""
    merged = dict(existing)
    added = 0
    updated = 0

    for entry in new_entries:
        key = entry["personal_email"].lower()
        if key in merged:
            old = merged[key]
            # Preserve alias from existing entry
            entry["alias"] = old.get("alias") or entry.get("alias") or ""
            # Update memberships from sheet (source of truth for group affiliations)
            merged[key] = entry
            updated += 1
        else:
            merged[key] = entry
            added += 1

    # Sort by lastname, then firstname for consistent output
    members = sorted(merged.values(), key=lambda m: (m.get("lastname", "").lower(), m.get("firstname", "").lower()))
    return members, added, updated


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Sync approved heroes from Google Sheets to heros.yaml")
    parser.add_argument("--sheet-id", required=True, help="Google Sheet ID")
    parser.add_argument("--range", required=True, help="Sheet range (e.g., 'Form Responses 1')")
    parser.add_argument("--sa-json", required=True, help="Path to Google SA JSON key file")
    parser.add_argument("--admin-email", required=True, help="Google Workspace admin email for impersonation")
    parser.add_argument("--existing", help="Path to existing heros.yaml to merge with")
    parser.add_argument("--output", default="-", help="Output path (default: stdout)")
    args = parser.parse_args()

    # Authenticate and read sheet
    print("Authenticating to Google Sheets API...")
    token = get_sheets_access_token(args.sa_json, args.admin_email)

    print(f"Reading sheet {args.sheet_id} range '{args.range}'...")
    rows = read_sheet(token, args.sheet_id, args.range)
    print(f"Read {len(rows)} rows from sheet")

    # Find column indices
    name_col = COLUMN_MAP["name"]
    email_col = COLUMN_MAP["email"]
    groups_col = COLUMN_MAP["groups"]
    approved_col = COLUMN_MAP["approved"]

    # Filter approved rows
    approved = [r for r in rows if r.get(approved_col, "").strip().lower() in ("yes", "ja")]
    print(f"Found {len(approved)} approved entries")

    if not approved:
        print("No approved entries found — nothing to sync")
        return

    # Process approved entries
    new_entries = []
    for row in approved:
        full_name = row.get(name_col, "").strip()
        personal_email = row.get(email_col, "").strip()
        groups_str = row.get(groups_col, "").strip()

        if not full_name or not personal_email:
            print(f"  Skipping row with missing name or email: {full_name!r} / {personal_email!r}", file=sys.stderr)
            continue

        firstname, lastname = parse_name(full_name)
        javabin_email = derive_email(firstname, lastname)
        memberships = map_groups(groups_str)

        entry = {
            "firstname": firstname,
            "lastname": lastname,
            "personal_email": personal_email,
            "javabin_google_email": javabin_email,
            "alias": "",
            "memberships": memberships,
        }
        new_entries.append(entry)
        print(f"  {full_name} -> {javabin_email} [{', '.join(memberships)}]")

    # Merge with existing
    existing = load_existing(args.existing)
    members, added, updated = merge_heroes(existing, new_entries)
    print(f"\nResult: {len(members)} total members ({added} added, {updated} updated)")

    # Output
    content = format_yaml(members)
    if args.output == "-":
        print(content)
    else:
        with open(args.output, "w") as f:
            f.write(content)
        print(f"Written to {args.output}")


if __name__ == "__main__":
    main()
