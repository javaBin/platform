#!/usr/bin/env python3
"""auth doctor — check that an app's Cognito sign-in works end to end.

Uses only what the app's team can read: app.yaml, the app's own
/javabin/apps/{team}/{app}/* parameters, and unauthenticated HTTP to the
pool's hosted UI. Teams have no cognito-idp access; app-auth-sync reports
what it did in the auth-status parameter instead.

Checks:
  1. The auth request parameter matches what app.yaml generates.
  2. auth-status says synced, for this request version, and its client ID is
     the one in cognito-client-id. Reports drift the platform corrected and
     allowed groups that do not exist (a typo there denies everyone).
  3. GET /oauth2/authorize for every callback URL redirects to the sign-in
     IdP (not /error?error=redirect_mismatch). For Google, also that the
     account chooser is forced (prompt=select_account).
  4. GET /logout for every logout URL redirects back to it. Cognito wants
     logout_uri to match a logout URL exactly (trailing slash included) and
     otherwise sends the user to its own /login or /error page.
  5. Best effort (WARN only): the service's PRIMARY-deployment tasks started
     after the client ID last changed (auth-status client_id_changed_at).

Usage:
  python3 scripts/auth-doctor.py [--app-yaml app.yaml] [--team T] [--app A]
                                 [--wait SECONDS] [--json]

Runs after `terraform apply` in tf-apply.yml (non-blocking); the javabin CLI
can wrap it as `javabin auth doctor` (--json gives machine-readable output).
Needs the aws CLI with credentials that can read the app's parameters.
Exit code 1 when a check fails, 0 otherwise (warnings included).
"""

import argparse
import datetime
import importlib.util
import json
import os
import subprocess
import sys
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlencode, urlsplit

HERE = os.path.dirname(os.path.abspath(__file__))
PROJECT = "javabin"
CLUSTER = f"{PROJECT}-platform"


def _load_generator():
    spec = importlib.util.spec_from_file_location("generate_modules", os.path.join(HERE, "generate-modules.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ---------------------------------------------------------------------------
# I/O (replaced in tests)
# ---------------------------------------------------------------------------

def aws(*args):
    """Run an aws CLI command; returns parsed JSON, or raises RuntimeError(stderr)."""
    r = subprocess.run(["aws", *args, "--output", "json"], capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or f"aws {' '.join(args)} failed")
    return json.loads(r.stdout or "null")


def ssm_get(name):
    """{Value, Version, LastModifiedDate} of a String parameter, or None if missing."""
    try:
        return aws("ssm", "get-parameter", "--name", name)["Parameter"]
    except RuntimeError as e:
        if "ParameterNotFound" in str(e):
            return None
        raise


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def http_get(url):
    """(status, Location header) without following redirects."""
    opener = urllib.request.build_opener(_NoRedirect)
    try:
        with opener.open(urllib.request.Request(url, method="GET"), timeout=15) as resp:
            return resp.status, resp.headers.get("Location", "")
    except urllib.error.HTTPError as e:
        return e.code, e.headers.get("Location", "")


# ---------------------------------------------------------------------------
# Checks
# ---------------------------------------------------------------------------

class Report:
    def __init__(self):
        self.results = []

    def add(self, level, check, message):
        self.results.append({"level": level, "check": check, "message": message})

    def ok(self, check, message):
        self.add("PASS", check, message)

    def warn(self, check, message):
        self.add("WARN", check, message)

    def fail(self, check, message):
        self.add("FAIL", check, message)

    @property
    def failed(self):
        return any(r["level"] == "FAIL" for r in self.results)


def expected_request(auth):
    return {
        "pool": auth["pool"],
        "client_type": auth["client"],
        "callback_urls": auth["callback_urls"],
        "logout_urls": auth["logout_urls"],
        "scopes": auth["scopes"],
        "allowed_groups": auth["groups"],
    }


def check_request(report, base, auth):
    param = ssm_get(f"{base}/auth")
    if param is None:
        report.fail("request", f"{base}/auth does not exist — has terraform apply run with the auth module?")
        return None
    try:
        actual = json.loads(param["Value"])
    except ValueError:
        report.fail("request", f"{base}/auth is not JSON")
        return None
    want = expected_request(auth)
    diff = sorted(k for k in want if actual.get(k, [] if isinstance(want[k], list) else None) != want[k])
    if diff:
        report.fail("request", f"{base}/auth differs from app.yaml in {', '.join(diff)} (not applied yet?)")
    else:
        report.ok("request", f"{base}/auth matches app.yaml (version {param.get('Version')})")
    return param


# auth-status missing altogether: the platform has never synced this app (no
# reconcile since the platform change, or a failed one). Don't sit out the
# full --wait for that; a first deploy's event sync takes seconds.
MISSING_STATUS_WAIT = 30


class StatusNotJSON(ValueError):
    pass


def read_status(base):
    param = ssm_get(f"{base}/auth-status")
    if not param:
        return None
    try:
        status = json.loads(param["Value"])
    except ValueError:
        raise StatusNotJSON(f"{base}/auth-status is not JSON")
    if not isinstance(status, dict):
        raise StatusNotJSON(f"{base}/auth-status is not a JSON object")
    return status


def wait_for_status(base, request_version, wait, sleep=time.sleep):
    """auth-status for this request version, polling up to `wait` seconds."""
    start = time.monotonic()
    while True:
        status = read_status(base)
        if status and (request_version is None or status.get("request_version") == request_version):
            return status, True
        limit = wait if status else min(wait, MISSING_STATUS_WAIT)
        if time.monotonic() - start >= limit:
            return status, False
        sleep(5)


def check_status(report, base, status, current):
    if status is None:
        report.fail("status", f"{base}/auth-status does not exist — app-auth-sync has not synced this app yet")
        return False
    if not current:
        report.warn("status", f"auth-status is for request version {status.get('request_version')}, "
                              f"not the current one (sync still running?) — checking the last result")
    if status.get("status") != "synced":
        report.fail("status", f"app-auth-sync: {status.get('status')}: {status.get('error')}")
        return False
    client_param = ssm_get(f"{base}/cognito-client-id")
    if client_param is None or client_param["Value"] != status.get("client_id"):
        report.fail("status", f"cognito-client-id ({client_param and client_param['Value']}) "
                              f"!= synced client {status.get('client_id')}")
        return False
    report.ok("status", f"synced {status.get('synced_at')}: client {status.get('client_id')} in "
                        f"{status.get('pool')} pool, sign-in via {', '.join(status.get('identity_providers') or [])}")
    if status.get("drift_corrected"):
        report.warn("drift", f"someone changed the client outside the platform; reverted: "
                             f"{', '.join(status['drift_corrected'])}")
    if status.get("unknown_groups"):
        report.fail("groups", f"auth.groups {status['unknown_groups']} do not exist in the registry "
                              f"memberships — nobody can sign in through them (typo?)")
    if status.get("empty_groups"):
        report.warn("groups", f"auth.groups {status['empty_groups']} have no members")
    return True


def probe_authorize(report, domain, client_id, idp, scopes, callback):
    query = {"response_type": "code", "client_id": client_id, "redirect_uri": callback,
             "scope": " ".join(scopes)}
    if idp and idp != "COGNITO":
        query["identity_provider"] = idp
    code, location = http_get(f"https://{domain}/oauth2/authorize?{urlencode(query)}")
    loc = urlsplit(location)
    if code not in (301, 302, 303, 307) or not location:
        report.fail("authorize", f"{callback}: HTTP {code} instead of a redirect")
        return
    if loc.hostname == domain and loc.path.startswith("/error"):
        err = parse_qs(loc.query)
        report.fail("authorize", f"{callback}: Cognito error {err.get('error', ['?'])[0]} "
                                 f"{err.get('error_description', [''])[0]}".rstrip())
        return
    if idp == "COGNITO":
        report.ok("authorize", f"{callback}: redirects to the hosted UI ({loc.path})")
        return
    if loc.hostname == domain:
        report.fail("authorize", f"{callback}: stays on the hosted UI ({loc.path}) instead of going to {idp}")
        return
    report.ok("authorize", f"{callback}: redirects to {idp} ({loc.hostname})")
    if loc.hostname == "accounts.google.com" and "/o/oauth2/" in loc.path:
        if parse_qs(loc.query).get("prompt") != ["select_account"]:
            report.warn("authorize", "Google is not asked to show the account chooser (prompt=select_account "
                                     "missing) — see docs/cognito-google-setup.md, managed-login fallback")


def probe_logout(report, domain, client_id, logout_url):
    """A matching logout_uri redirects straight back to it; a mismatch lands on
    the hosted UI (/login or /error) — checked live against the classic UI."""
    code, location = http_get(f"https://{domain}/logout?{urlencode({'client_id': client_id, 'logout_uri': logout_url})}")
    loc = urlsplit(location)
    if code not in (301, 302, 303, 307) or not location:
        report.fail("logout", f"{logout_url}: HTTP {code} instead of a redirect")
    elif location == logout_url or location.startswith(logout_url):
        report.ok("logout", f"{logout_url}: redirects back to the app")
    elif loc.hostname == domain:
        report.fail("logout", f"{logout_url}: Cognito sends the user to {loc.path} instead — logout_uri must "
                              f"match a logout URL exactly (trailing slash, path)")
    else:
        report.warn("logout", f"{logout_url}: redirects to {loc.scheme}://{loc.hostname}{loc.path} (IdP sign-out?)")


def check_tasks(report, team, app, status):
    """Best effort: PRIMARY-deployment tasks that started before the client ID
    last changed still hold the old one. WARN only — the timestamp is the
    platform's record of a real ID change, absent for apps not resynced since."""
    changed_at = status.get("client_id_changed_at")
    if not changed_at:
        report.add("SKIP", "tasks", "auth-status has no client_id_changed_at yet; skipping the task check")
        return
    try:
        services = aws("ecs", "describe-services", "--cluster", CLUSTER, "--services", f"{team}-{app}")["services"]
        primary = next((d["id"] for s in services for d in s.get("deployments", []) if d.get("status") == "PRIMARY"),
                       None)
        arns = aws("ecs", "list-tasks", "--cluster", CLUSTER, "--service-name", f"{team}-{app}")["taskArns"]
        if not arns:
            report.warn("tasks", f"no running tasks in {team}-{app}")
            return
        tasks = aws("ecs", "describe-tasks", "--cluster", CLUSTER, "--tasks", *arns)["tasks"]
    except (RuntimeError, KeyError) as e:
        report.add("SKIP", "tasks", f"could not read ECS tasks: {str(e).splitlines()[0][:160]}")
        return
    if primary:
        # Tasks of a deployment that is still draining are replaced anyway
        tasks = [t for t in tasks if t.get("startedBy") == primary]
    changed = _ts(changed_at)
    stale = [t["taskArn"].rsplit("/", 1)[-1] for t in tasks if t.get("startedAt") and _ts(t["startedAt"]) < changed]
    if stale:
        report.warn("tasks", f"{len(stale)} task(s) started before the client ID changed ({changed_at}) and may "
                             f"use the old client: {', '.join(stale)} — app-secret-rollout should be restarting them")
    else:
        report.ok("tasks", f"{len(tasks)} running task(s) started after the client ID last changed")


def _ts(value):
    if isinstance(value, (int, float)):
        return float(value)
    return datetime.datetime.fromisoformat(str(value).replace("Z", "+00:00")).timestamp()


def diagnose(app, team=None, name=None, wait=0, tasks=True, sleep=time.sleep):
    gen = _load_generator()
    report = Report()
    team = team or app.get("team")
    name = name or app.get("name")
    auth = gen.resolve_auth(app)
    if not auth:
        report.add("SKIP", "auth", "app.yaml has no auth section")
        return report
    if auth["client"] == "none":
        report.add("SKIP", "auth", "client: none — validate-only app, no Cognito client to check")
        return report

    base = f"/{PROJECT}/apps/{team}/{name}"
    request = check_request(report, base, auth)
    if request is None:
        return report
    try:
        status, current = wait_for_status(base, request.get("Version"), wait, sleep)
    except StatusNotJSON as e:
        report.fail("status", str(e))
        return report
    if not check_status(report, base, status, current):
        return report

    domain = status.get("pool_domain")
    if not domain:
        report.warn("authorize", f"the {status.get('pool')} pool has no hosted-UI domain; skipping HTTP probes")
    else:
        for cb in status.get("callback_urls") or auth["callback_urls"]:
            probe_authorize(report, domain, status["client_id"], status.get("identity_provider"),
                            status.get("scopes") or auth["scopes"], cb)
        for lo in status.get("logout_urls") or auth["logout_urls"]:
            probe_logout(report, domain, status["client_id"], lo)
    if tasks:
        check_tasks(report, team, name, status)
    return report


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--app-yaml", default="app.yaml")
    p.add_argument("--team", help="default: app.yaml team")
    p.add_argument("--app", help="default: app.yaml name, then $APP_SERVICE, then the repository name")
    p.add_argument("--wait", type=int, default=0, help="seconds to wait for app-auth-sync to finish (CI: 180)")
    p.add_argument("--no-tasks", action="store_true", help="skip the ECS task check")
    p.add_argument("--json", action="store_true", help="print results as JSON")
    args = p.parse_args(argv)

    if not os.path.exists(args.app_yaml):
        print(f"No {args.app_yaml}; nothing to check")
        return 0
    gen = _load_generator()
    app = gen.load_yaml(args.app_yaml) or {}
    name = args.app or app.get("name") or os.environ.get("APP_SERVICE") or \
        os.environ.get("GITHUB_REPOSITORY", "").rsplit("/", 1)[-1]
    try:
        report = diagnose(app, args.team, name, args.wait, tasks=not args.no_tasks)
    except gen.AppYamlError as e:
        print(f"app.yaml: {e}", file=sys.stderr)
        return 1

    if args.json:
        print(json.dumps({"ok": not report.failed, "results": report.results}, indent=2))
    else:
        for r in report.results:
            line = f"{r['level']:4}  {r['check']:9}  {r['message']}"
            if r["level"] == "FAIL" and os.environ.get("GITHUB_ACTIONS"):
                line = f"::error title=auth doctor ({r['check']})::{r['message']}"
            elif r["level"] == "WARN" and os.environ.get("GITHUB_ACTIONS"):
                line = f"::warning title=auth doctor ({r['check']})::{r['message']}"
            print(line)
        print("auth doctor: " + ("FAILED" if report.failed else "OK"))
    return 1 if report.failed else 0


if __name__ == "__main__":
    sys.exit(main())
