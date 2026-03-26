#!/usr/bin/env python3
"""Reconcile Google Workspace state against members.yaml — plan or apply removals.

Two-phase workflow:
  1. Plan: compare live Google Workspace state against YAML, output a plan
  2. Apply: execute the plan (suspend accounts, remove group memberships)

Usage:
  reconcile-groups.py --plan --members M --groups G --access A --sa-json SA --admin-email E [--plan-file plan.json]
  reconcile-groups.py --apply --plan-file plan.json --sa-json SA --admin-email E

Requires: aws CLI (for Cognito/IC), openssl (for JWT signing)
"""

import argparse
import base64
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request

import yaml

MAX_SUSPENSIONS = 10  # refuse to suspend more than this without --force


# ---------------------------------------------------------------------------
# Google Auth (same pattern as sync-members.py)
# ---------------------------------------------------------------------------

ADMIN_SCOPES = (
    "https://www.googleapis.com/auth/admin.directory.user "
    "https://www.googleapis.com/auth/admin.directory.group "
    "https://www.googleapis.com/auth/admin.directory.group.member"
)


def _b64url(data):
    if isinstance(data, str):
        data = data.encode("utf-8")
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def _sign_rs256(message_bytes, private_key_pem):
    with tempfile.NamedTemporaryFile(mode="w", suffix=".pem", delete=True) as f:
        f.write(private_key_pem)
        f.flush()
        r = subprocess.run(["openssl", "dgst", "-sha256", "-sign", f.name],
                           input=message_bytes, capture_output=True)
    if r.returncode != 0:
        raise RuntimeError(f"openssl signing failed: {r.stderr.decode()}")
    return r.stdout


def get_access_token(sa_json_path, admin_email, scopes=ADMIN_SCOPES):
    with open(sa_json_path) as f:
        sa = json.load(f)
    now = int(time.time())
    header = _b64url('{"alg":"RS256","typ":"JWT"}')
    payload = _b64url(json.dumps({
        "iss": sa["client_email"], "sub": admin_email,
        "scope": scopes, "aud": "https://oauth2.googleapis.com/token",
        "iat": now, "exp": now + 3600,
    }))
    sig = _b64url(_sign_rs256(f"{header}.{payload}".encode(), sa["private_key"]))
    data = urllib.parse.urlencode({
        "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer",
        "assertion": f"{header}.{payload}.{sig}",
    }).encode()
    req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data,
                                headers={"Content-Type": "application/x-www-form-urlencoded"})
    return json.loads(urllib.request.urlopen(req).read())["access_token"]


def google_api(method, path, token, body=None):
    url = f"https://admin.googleapis.com/admin/directory/v1{path}"
    data = json.dumps(body).encode() if body else None
    req = urllib.request.Request(url, data=data, method=method)
    req.add_header("Authorization", f"Bearer {token}")
    req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req) as resp:
            raw = resp.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        body_text = e.read().decode("utf-8", errors="replace")
        if e.code == 404:
            return None
        raise RuntimeError(f"Google API {method} {path} → {e.code}: {body_text}")


# ---------------------------------------------------------------------------
# Google Workspace queries
# ---------------------------------------------------------------------------

def list_workspace_users(token, domain="java.no"):
    users = []
    page_token = None
    while True:
        path = f"/users?domain={domain}&maxResults=500"
        if page_token:
            path += f"&pageToken={page_token}"
        resp = google_api("GET", path, token)
        if resp and "users" in resp:
            users.extend(resp["users"])
        if resp and resp.get("nextPageToken"):
            page_token = resp["nextPageToken"]
        else:
            break
    return users


def list_group_members(token, group_email):
    members = []
    group_key = urllib.parse.quote(group_email, safe="")
    page_token = None
    while True:
        path = f"/groups/{group_key}/members?maxResults=200"
        if page_token:
            path += f"&pageToken={urllib.parse.quote(page_token, safe='')}"
        resp = google_api("GET", path, token)
        if resp and "members" in resp:
            members.extend(resp["members"])
        if resp and resp.get("nextPageToken"):
            page_token = resp["nextPageToken"]
        else:
            break
    return members


# ---------------------------------------------------------------------------
# Plan generation
# ---------------------------------------------------------------------------

def generate_plan(token, members_data, groups_data, access_data):
    plan = {"actions": [], "generated_at": int(time.time())}

    # Hash members.yaml for staleness detection
    members_json = json.dumps(members_data, sort_keys=True)
    plan["members_sha"] = hashlib.sha256(members_json.encode()).hexdigest()

    desired_emails = {
        m["javabin_google_email"].lower()
        for m in (members_data.get("members") or [])
        if m.get("javabin_google_email")
    }

    # 1. Find users to suspend (in Google but not in YAML)
    print("Fetching Google Workspace users...")
    ws_users = list_workspace_users(token)
    for user in ws_users:
        email = user.get("primaryEmail", "").lower()
        if email not in desired_emails and not user.get("suspended"):
            # Skip system/admin accounts
            if email.startswith("admin@") or email.startswith("postmaster@"):
                continue
            plan["actions"].append({
                "type": "suspend_user",
                "email": email,
                "name": f"{user.get('name', {}).get('givenName', '')} {user.get('name', {}).get('familyName', '')}",
            })

    # 2. Find group membership removals
    print("Checking group memberships...")
    members_list = members_data.get("members") or []
    for group in (groups_data.get("groups") or []):
        group_email = group.get("google", "")
        if not group_email:
            continue

        desired_group_members = {
            m["javabin_google_email"].lower()
            for m in members_list
            if m.get("javabin_google_email") and group["name"] in (m.get("memberships") or [])
        }

        try:
            current_members = list_group_members(token, group_email)
        except Exception as e:
            print(f"  Warning: could not list members of {group_email}: {e}", file=sys.stderr)
            continue

        for member in current_members:
            member_email = member.get("email", "").lower()
            # Only remove individual users (not nested groups)
            if member.get("type") == "GROUP":
                continue
            if member_email not in desired_group_members and member_email in desired_emails:
                # Member exists in YAML but shouldn't be in this group
                plan["actions"].append({
                    "type": "remove_from_group",
                    "email": member_email,
                    "group": group["name"],
                    "group_email": group_email,
                })

    return plan


def format_plan_summary(plan):
    actions = plan["actions"]
    suspensions = [a for a in actions if a["type"] == "suspend_user"]
    removals = [a for a in actions if a["type"] == "remove_from_group"]

    lines = ["## Reconciliation Plan", ""]

    if not actions:
        lines.append("No changes needed — Google Workspace is in sync with members.yaml.")
        return "\n".join(lines)

    if suspensions:
        lines.append(f"### Accounts to Suspend ({len(suspensions)})")
        lines.append("| Name | Email |")
        lines.append("|------|-------|")
        for s in suspensions:
            lines.append(f"| {s['name']} | {s['email']} |")
        lines.append("")

    if removals:
        lines.append(f"### Group Membership Removals ({len(removals)})")
        for r in removals:
            lines.append(f"- Remove **{r['email']}** from **{r['group']}** ({r['group_email']})")
        lines.append("")

    lines.append(f"*Generated at {time.strftime('%Y-%m-%d %H:%M UTC', time.gmtime(plan['generated_at']))}*")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Apply
# ---------------------------------------------------------------------------

def apply_plan(plan, token, members_data, force=False):
    # Staleness check
    members_json = json.dumps(members_data, sort_keys=True)
    current_sha = hashlib.sha256(members_json.encode()).hexdigest()
    if plan.get("members_sha") and plan["members_sha"] != current_sha:
        print("ERROR: members.yaml has changed since the plan was generated. Re-run with --plan first.", file=sys.stderr)
        sys.exit(1)

    actions = plan["actions"]
    suspensions = [a for a in actions if a["type"] == "suspend_user"]

    if len(suspensions) > MAX_SUSPENSIONS and not force:
        print(f"ERROR: {len(suspensions)} suspensions exceeds safety limit of {MAX_SUSPENSIONS}.", file=sys.stderr)
        print("Use --force to override.", file=sys.stderr)
        sys.exit(1)

    results = {"suspended": [], "removed": [], "failed": []}

    for action in actions:
        if action["type"] == "suspend_user":
            email = action["email"]
            user_key = urllib.parse.quote(email, safe="")
            try:
                google_api("PATCH", f"/users/{user_key}", token, {"suspended": True})
                results["suspended"].append(email)
                print(f"  Suspended: {email}")
            except Exception as e:
                results["failed"].append({"action": action, "error": str(e)[:200]})
                print(f"  FAILED to suspend {email}: {e}", file=sys.stderr)

        elif action["type"] == "remove_from_group":
            email = action["email"]
            group_email = action["group_email"]
            group_key = urllib.parse.quote(group_email, safe="")
            member_key = urllib.parse.quote(email, safe="")
            try:
                google_api("DELETE", f"/groups/{group_key}/members/{member_key}", token)
                results["removed"].append(f"{email} from {action['group']}")
                print(f"  Removed: {email} from {action['group']}")
            except Exception as e:
                results["failed"].append({"action": action, "error": str(e)[:200]})
                print(f"  FAILED to remove {email} from {action['group']}: {e}", file=sys.stderr)

    print(f"\nResults: {len(results['suspended'])} suspended, {len(results['removed'])} removed, {len(results['failed'])} failed")
    return results


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Reconcile Google Workspace against members.yaml")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true", help="Generate a reconciliation plan")
    mode.add_argument("--apply", action="store_true", help="Apply a reconciliation plan")

    parser.add_argument("--members", help="Path to members.yaml")
    parser.add_argument("--groups", help="Path to groups.yaml")
    parser.add_argument("--access", help="Path to access.yaml")
    parser.add_argument("--sa-json", required=True, help="Path to Google SA JSON key file")
    parser.add_argument("--admin-email", required=True, help="Google admin email for impersonation")
    parser.add_argument("--plan-file", help="Path for plan JSON (output in --plan, input in --apply)")
    parser.add_argument("--summary-file", help="Path for Markdown summary (--plan only)")
    parser.add_argument("--force", action="store_true", help="Override safety limits")
    args = parser.parse_args()

    token = get_access_token(args.sa_json, args.admin_email)

    if args.plan:
        if not args.members or not args.groups:
            parser.error("--plan requires --members and --groups")

        members_data = yaml.safe_load(open(args.members)) or {}
        groups_data = yaml.safe_load(open(args.groups)) or {}
        access_data = yaml.safe_load(open(args.access)) or {} if args.access else {}

        plan = generate_plan(token, members_data, groups_data, access_data)
        summary = format_plan_summary(plan)

        print(summary)

        if args.plan_file:
            with open(args.plan_file, "w") as f:
                json.dump(plan, f, indent=2)
            print(f"\nPlan written to {args.plan_file}")

        if args.summary_file:
            with open(args.summary_file, "w") as f:
                f.write(summary)

        if plan["actions"]:
            sys.exit(2)  # nonzero = changes needed

    elif args.apply:
        if not args.plan_file:
            parser.error("--apply requires --plan-file")

        with open(args.plan_file) as f:
            plan = json.load(f)

        members_data = yaml.safe_load(open(args.members)) or {} if args.members else {}
        apply_plan(plan, token, members_data, force=args.force)


if __name__ == "__main__":
    main()
