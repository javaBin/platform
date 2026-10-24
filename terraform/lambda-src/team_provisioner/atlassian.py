"""Atlassian group sync — registry groups → Atlassian (Confluence) groups.

The registry's groups/atlassian.yaml maps each Atlassian group to registry
groups. provision-atlassian.py resolves that to java.no emails, and this module
makes each declared Atlassian group contain exactly those accounts:

- members are matched by email against the org directory, so only accounts
  the Google Workspace user sync created (managed java.no accounts) are added
- anyone else in a declared group is removed, including personal-email
  accounts; groups not declared in the YAML are never touched
- report mode (the default) computes the same plan without writing

Uses the Atlassian organization admin REST API (v2) with an org API key.
"""

import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request

logger = logging.getLogger(__name__)

API_BASE = "https://api.atlassian.com/admin/v2"

# Membership of these groups doesn't grant the role via the API, and getting it
# wrong locks admins out — manage them by hand in admin.atlassian.com.
PROTECTED_GROUPS = {"org-admins", "site-admins"}

_MAX_RETRIES = 4


class AtlassianAdmin:
    """Thin client for the org admin API, scoped to the org's single directory."""

    def __init__(self, org_id, api_key, urlopen=urllib.request.urlopen, sleep=time.sleep):
        self._org = org_id
        self._key = api_key
        self._urlopen = urlopen
        self._sleep = sleep
        self._directory = None

    def _request(self, method, path, body=None, params=None):
        url = f"{API_BASE}/orgs/{self._org}{path}"
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body is not None else None
        headers = {"Authorization": f"Bearer {self._key}", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"

        for attempt in range(_MAX_RETRIES + 1):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with self._urlopen(req) as resp:
                    raw = resp.read()
                    return json.loads(raw) if raw else {}
            except urllib.error.HTTPError as e:
                retryable = e.code == 429 or e.code >= 500
                if not retryable or attempt == _MAX_RETRIES:
                    detail = e.read().decode(errors="replace")[:300]
                    raise RuntimeError(f"{method} {path} → {e.code}: {detail}") from e
                retry_after = e.headers.get("Retry-After") if e.headers else None
                self._sleep(float(retry_after) if retry_after else 2 ** attempt)

    def _paged(self, path, params=None):
        params = dict(params or {}, limit=100)
        while True:
            resp = self._request("GET", path, params=params)
            yield from resp.get("data") or []
            cursor = (resp.get("links") or {}).get("next")
            if not cursor:
                return
            params["cursor"] = cursor

    @property
    def directory(self):
        if self._directory is None:
            dirs = self._request("GET", "/directories").get("data") or []
            if len(dirs) != 1:
                raise RuntimeError(f"expected one directory, found {len(dirs)}")
            self._directory = dirs[0]["directoryId"]
        return self._directory

    def users(self, group_id=None):
        params = {"groupIds": group_id} if group_id else None
        return list(self._paged(f"/directories/{self.directory}/users", params))

    def groups(self):
        return list(self._paged(f"/directories/{self.directory}/groups"))

    def create_group(self, name, description=""):
        body = {"name": name, "description": description}
        return self._request("POST", f"/directories/{self.directory}/groups", body)

    def add_member(self, group_id, account_id):
        self._request(
            "POST", f"/directories/{self.directory}/groups/{group_id}/memberships",
            {"accountId": account_id},
        )

    def remove_member(self, group_id, account_id):
        self._request(
            "DELETE", f"/directories/{self.directory}/groups/{group_id}/memberships/{account_id}",
        )


def _email(user):
    return (user.get("email") or "").lower()


def plan_group(desired_emails, current_members, accounts_by_email):
    """Compute membership changes for one group.

    desired_emails: java.no emails the registry says belong in the group
    current_members: directory users currently in the group
    accounts_by_email: every directory user, keyed by lowercase email
    """
    desired = {e.lower() for e in desired_emails}
    current_ids = {u["accountId"] for u in current_members}

    add = sorted(
        (accounts_by_email[e] for e in desired if e in accounts_by_email
         and accounts_by_email[e]["accountId"] not in current_ids),
        key=_email,
    )
    remove = sorted((u for u in current_members if _email(u) not in desired), key=_email)
    missing = sorted(e for e in desired if e not in accounts_by_email)
    return {"add": add, "remove": remove, "missing_account": missing}


def _describe(user):
    label = _email(user) or user["accountId"]
    return f"{label} (personal)" if user.get("claimStatus") == "unmanaged" else label


def sync(admin, groups, apply=False):
    """Plan, and if apply is set, enforce each declared group's membership.

    groups: [{"name": str, "description": str, "members": [email, ...]}]
    Returns per-group results; one failing group doesn't stop the others.
    """
    accounts_by_email = {_email(u): u for u in admin.users() if _email(u)}
    existing = {g["name"]: g for g in admin.groups()}
    results = {}

    for group in groups:
        name = group["name"]
        try:
            if name in PROTECTED_GROUPS:
                raise ValueError(f"{name} is protected — manage it in admin.atlassian.com")
            current = existing.get(name)
            if current and current.get("externalSynced"):
                raise ValueError(f"{name} is synced from an identity provider and can't be edited")

            members = admin.users(current["id"]) if current else []
            plan = plan_group(group.get("members") or [], members, accounts_by_email)
            result = {
                "create": current is None,
                "add": [_describe(u) for u in plan["add"]],
                "remove": [_describe(u) for u in plan["remove"]],
                "missing_account": plan["missing_account"],
                "applied": False,
            }

            if apply:
                group_id = (
                    admin.create_group(name, group.get("description") or "")["id"]
                    if current is None else current["id"]
                )
                for u in plan["add"]:
                    admin.add_member(group_id, u["accountId"])
                for u in plan["remove"]:
                    admin.remove_member(group_id, u["accountId"])
                result["applied"] = True

            results[name] = result
        except (RuntimeError, ValueError, KeyError) as e:
            logger.error("Atlassian sync failed for %s: %s", name, e)
            results[name] = {"error": str(e)[:300]}

    return results


def summary_text(results, apply):
    """One Slack-friendly line per group, counts only (no email addresses)."""
    mode = "applied" if apply else "report only — nothing changed"
    lines = [f"*Atlassian group sync* ({mode})"]
    for name, r in sorted(results.items()):
        if "error" in r:
            lines.append(f"• `{name}`: :x: {r['error']}")
            continue
        personal = sum(1 for d in r["remove"] if d.endswith("(personal)"))
        parts = [f"+{len(r['add'])}", f"-{len(r['remove'])} ({personal} personal)"]
        if r["missing_account"]:
            parts.append(f"{len(r['missing_account'])} without Atlassian account")
        if r["create"]:
            parts.insert(0, "new group")
        lines.append(f"• `{name}`: " + ", ".join(parts))
    return "\n".join(lines)
