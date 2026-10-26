#!/usr/bin/env python3
"""Generate Terraform module blocks from app.yaml.

Reads app.yaml and produces .tf files with module {} blocks. Terraform handles
variable resolution, cross-module references, and output wiring natively.

app.yaml is validated first: unknown or unsupported fields are an error, so a
typo or a not-yet-implemented feature fails the pipeline with a clear message
instead of being silently ignored.

The output is `terraform fmt`-clean, because tf-plan runs `terraform fmt -check`.

Usage:
  python3 generate-modules.py

Required env vars: APP_SERVICE, AWS_ACCOUNT_ID, AWS_REGION, TF_ROOT
Optional env vars: PLATFORM_REF (git ref for module source, default: main)
"""

import json
import os
import re
import subprocess
import sys
from urllib.parse import urlsplit

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from registry import (
    PROJECT, DOMAIN, APP_DOMAINS, MODULE_SOURCES, ADOPTIONS,
    BACKEND_TEMPLATE, PROVIDERS_TEMPLATE, OUTPUTS_TEMPLATE, GITIGNORE_TEMPLATE,
)

GENERATED = "# GENERATED FROM app.yaml — do not edit, changes will be overwritten\n"

# ---------------------------------------------------------------------------
# YAML loading
# ---------------------------------------------------------------------------

def load_yaml(path):
    result = subprocess.run(["yq", "-o", "json", path], capture_output=True, text=True)
    if result.returncode != 0:
        try:
            import yaml
            with open(path) as f:
                return yaml.safe_load(f)
        except ImportError:
            raise RuntimeError(f"Failed to parse {path}: yq not found and pyyaml not installed")
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

class AppYamlError(ValueError):
    """app.yaml is invalid; the message is shown to the developer as-is."""


SCHEMA = {
    "name": None,
    "team": None,
    "compute": {"cpu", "memory", "port", "desired_count", "health_check",
                "health_check_matcher", "user", "readonly_root_filesystem"},
    "routing": {"host", "priority", "deregistration_delay", "login", "adopt_dns"},
    "domain": None,
    "resources": {"buckets", "databases", "secrets", "queues"},
    "auth": {"pool", "client", "callback_urls", "logout_urls", "scopes", "groups", "env", "extra_hosts"},
    "environment": None,
    "alarms": {"enabled", "cpu_threshold", "memory_threshold", "error_5xx_threshold"},
    "calls": None,
    "provides": {"scopes", "consumers"},
}

# App-to-app calls (see docs/app-yaml-reference.md#app-to-app-calls)
CALL_SCHEMA = {"app", "team", "scopes", "env"}
CONSUMER_SCHEMA = {"app", "team", "scopes"}
MAX_CALLS, MAX_CONSUMERS, MAX_SCOPES = 20, 50, 25

RESOURCE_SCHEMA = {
    "buckets": {"name", "env", "versioning", "expire_days"},
    "databases": {"name", "env", "engine", "hash_key", "hash_key_type", "range_key",
                  "range_key_type", "ttl_attribute", "instance_class", "allocated_storage",
                  "engine_version", "backup_retention_period", "multi_az", "deletion_protection",
                  "adopt", "host_env", "port_env"},
    "secrets": {"name", "env", "description"},
    "queues": {"name", "env", "visibility_timeout", "retention_seconds", "max_receive_count"},
}

# Documented at some point but not implemented. Named here so the error says
# what to do instead of "unknown field".
UNSUPPORTED = {
    "environments": "multi-environment apps are not supported yet; deploy one environment per app.yaml",
    "budget_alert_nok": "budgets are per team — set budget_nok in javaBin/registry teams/<team>.yaml",
}

AUTH_CLIENT_TYPES = ("confidential", "public", "none")
AUTH_POOLS = ("internal", "external")

# routing.login — sign-in enforced by the ALB (authenticate-oidc) per path.
LOGIN_KEYS = {"paths", "pool", "groups", "public"}
LOGIN_MAX_RULES = 10
LOGIN_MAX_PATHS = 4  # an ALB rule takes 5 condition values; the host is one
# ALB path-pattern characters (case-sensitive, * and ? wildcards, max 128 chars)
LOGIN_PATH_RE = re.compile(r"^/[A-Za-z0-9_\-.$/~\"'@:+*?&]{0,127}$")
GROUP_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,127}$")

NAME_RE = re.compile(r"^[a-z][a-z0-9-]{0,19}$")
TEAM_RE = re.compile(r"^[a-z]{1,12}$")
RESOURCE_NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_-]*$")
ENV_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
HOST_LABEL_RE = re.compile(r"^[a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?$")
HOSTNAME_RE = re.compile(r"^(?=.{1,253}$)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")
LOCAL_HOSTS = ("localhost", "127.0.0.1")

RDS_IDENTIFIER_RE = re.compile(r"^[a-zA-Z][a-zA-Z0-9-]{0,62}$")

POSTGRES_ENGINES = ("postgres", "postgresql")
# An adopted database keeps its live shape; these would suggest otherwise.
ADOPT_REJECTED = ("instance_class", "allocated_storage", "engine_version", "multi_az")

# Cognito scope names may not contain "/", quotes, backslashes or spaces
SCOPE_RE = re.compile(r"^[a-z][a-z0-9._-]{0,39}$")

# Injected when the app calls a peer with scopes / provides scopes
PEER_CALLER_AUTH_ENV = ("PEER_AUTH_TOKEN_URL", "PEER_AUTH_CLIENT_ID", "PEER_AUTH_CLIENT_SECRET")
PEER_CALLEE_AUTH_ENV = ("PEER_AUTH_ISSUER", "PEER_AUTH_JWKS_URL", "PEER_AUTH_SCOPE_PREFIX")


def host_zone(host):
    """The app domain a host lives under, or None if it is not one label under one."""
    label, _, zone = str(host).partition(".")
    return zone if zone in APP_DOMAINS and HOST_LABEL_RE.match(label) else None


def _check_keys(section, data, allowed):
    if not isinstance(data, dict):
        raise AppYamlError(f"{section}: expected a mapping, got {type(data).__name__}")
    unknown = sorted(set(data) - set(allowed))
    if unknown:
        raise AppYamlError(
            f"{section}: unknown field(s) {', '.join(unknown)} "
            f"(supported: {', '.join(sorted(allowed))})"
        )


def normalize_login(routing):
    """routing.login as a validated list of {paths, pool, groups, public}.

    Accepts `login: internal|external`, a single mapping, or a list of
    mappings. Rules are matched in the listed order; the first match wins.
    """
    raw = routing.get("login")
    if raw is None or raw is False or raw == []:
        return []
    if isinstance(raw, str):
        if raw not in AUTH_POOLS:
            raise AppYamlError(f"routing.login: {raw!r} must be 'internal', 'external', a mapping or a list")
        raw = [{"pool": raw}]
    elif isinstance(raw, dict):
        raw = [raw]
    if not isinstance(raw, list):
        raise AppYamlError("routing.login: expected 'internal', 'external', a mapping or a list of mappings")
    if len(raw) > LOGIN_MAX_RULES:
        raise AppYamlError(f"routing.login: at most {LOGIN_MAX_RULES} rules (got {len(raw)})")

    rules, seen, catch_all = [], set(), None
    for i, entry in enumerate(raw):
        where = f"routing.login[{i}]"
        _check_keys(where, entry, LOGIN_KEYS)
        if catch_all is not None:
            raise AppYamlError(f"{where}: unreachable — routing.login[{catch_all}] already matches /*")

        paths = entry.get("paths", ["/*"])
        if isinstance(paths, str):
            paths = [paths]
        if not isinstance(paths, list) or not 1 <= len(paths) <= LOGIN_MAX_PATHS:
            raise AppYamlError(f"{where}.paths: expected a list of 1-{LOGIN_MAX_PATHS} paths")
        for path in paths:
            if not isinstance(path, str) or not LOGIN_PATH_RE.match(path):
                raise AppYamlError(
                    f"{where}.paths: {path!r} must start with / and use only ALB path characters "
                    "(letters, digits, _-.$/~\"'@:+& and the wildcards * ?), max 128 chars"
                )
            if path in seen:
                raise AppYamlError(f"{where}.paths: {path!r} is listed more than once")
            seen.add(path)

        public = entry.get("public", False)
        if not isinstance(public, bool):
            raise AppYamlError(f"{where}.public: expected true or false")
        if public and ("pool" in entry or "groups" in entry):
            raise AppYamlError(f"{where}: public: true cannot be combined with pool or groups")

        pool = entry.get("pool", "internal")
        if pool not in AUTH_POOLS:
            raise AppYamlError(f"{where}.pool: must be 'internal' or 'external' (got {pool!r})")

        groups = entry.get("groups") or []
        if not isinstance(groups, list) or not all(isinstance(g, str) and GROUP_RE.match(g) for g in groups):
            raise AppYamlError(f"{where}.groups: expected a list of group names")

        rules.append({"paths": paths, "pool": pool, "groups": groups, "public": public})
        if "/*" in paths:
            catch_all = i

    # /admin/* does not match /admin itself: protect the bare prefix too,
    # unless the developer listed it somewhere explicitly.
    for i, rule in enumerate(rules):
        if rule["public"]:
            continue
        bare = [p[:-2] for p in rule["paths"] if p.endswith("/*") and p != "/*" and p[:-2] not in seen]
        if bare:
            seen.update(bare)
            rule["paths"] = rule["paths"] + bare
            if len(rule["paths"]) > LOGIN_MAX_PATHS:
                raise AppYamlError(
                    f"routing.login[{i}].paths: with the bare prefixes added ({', '.join(bare)} — /x/* does not "
                    f"match /x) this rule needs more than {LOGIN_MAX_PATHS} paths; split it into several entries"
                )

    priority = routing.get("priority")
    if isinstance(priority, int) and priority - len(rules) < 1:
        raise AppYamlError(
            f"routing.priority: {priority} leaves no room for {len(rules)} login rule(s) — "
            f"they use the priorities directly below it; use at least {len(rules) + 1}"
        )
    return rules


def login_warnings(rules):
    """Advice that does not fail the build."""
    protected = [r for r in rules if not r["public"]]
    if protected and not any("/*" in r["paths"] for r in rules):
        return [
            "routing.login protects only some paths. The load balancer matches the raw request path, "
            "case-sensitively and without decoding or normalizing it, so an app that does any of that can "
            "be reached past the rule (e.g. /Admin, /%61dmin or /x/../admin vs /admin/*). "
            "Prefer protecting /* and listing the open paths with public: true."
        ]
    return []


def _check_adoption(team, kind, identifier, where):
    if identifier not in ADOPTIONS.get(team, {}).get(kind, set()):
        raise AppYamlError(
            f"{where}: team {team!r} is not approved to adopt {identifier!r}. Adopting takes over a "
            "resource the platform did not create, so each one is approved in a javaBin/platform PR "
            f"that adds it to ADOPTIONS[{team!r}][{kind!r}] in scripts/registry.py"
        )


def _validate_database(where, db, team):
    postgres = str(db.get("engine") or "dynamodb").lower() in POSTGRES_ENGINES
    for key in ("host_env", "port_env"):
        if key in db:
            if not postgres:
                raise AppYamlError(f"{where}.{key}: only supported for engine: postgres")
            if not ENV_RE.match(str(db[key])):
                raise AppYamlError(f"{where}.{key}: {db[key]!r} is not a valid variable name")
    if "adopt" not in db:
        return
    if not postgres:
        raise AppYamlError(f"{where}.adopt: only supported for engine: postgres")
    if not RDS_IDENTIFIER_RE.match(str(db["adopt"])):
        raise AppYamlError(f"{where}.adopt: {db['adopt']!r} is not an RDS DB instance identifier")
    _check_adoption(team, "rds", db["adopt"], f"{where}.adopt")
    rejected = [k for k in ADOPT_REJECTED if k in db]
    if rejected:
        raise AppYamlError(
            f"{where}: {', '.join(rejected)} cannot be set with adopt — an adopted database keeps "
            "its live engine, class, storage and placement; change those outside app.yaml"
        )


def validate(app):
    """Raise AppYamlError if app.yaml uses unknown fields or invalid values."""
    if not isinstance(app, dict):
        raise AppYamlError("app.yaml must be a mapping")

    for key, reason in UNSUPPORTED.items():
        if key in app:
            raise AppYamlError(f"{key}: not supported — {reason}")
    _check_keys("app.yaml", app, SCHEMA)
    for section, allowed in SCHEMA.items():
        if allowed is not None and section in app and not (section == "auth" and isinstance(app[section], str)):
            _check_keys(section, app[section], allowed)

    if not NAME_RE.match(str(app.get("name", ""))):
        raise AppYamlError(f"name: {app.get('name')!r} must be lowercase letters, digits and hyphens, max 20 chars")
    if not TEAM_RE.match(str(app.get("team", ""))):
        raise AppYamlError(f"team: {app.get('team')!r} must be lowercase letters only, max 12 chars")

    routing = app.get("routing") or {}
    if routing:
        if not routing.get("host") and not app.get("domain"):
            raise AppYamlError("routing.host is required when routing is set")
        host = routing.get("host") or app.get("domain")
        if not host_zone(host):
            raise AppYamlError(
                f"routing.host: {host!r} must be one label under "
                f"{', '.join(APP_DOMAINS)} (e.g. my-app.{APP_DOMAINS[0]})"
            )
        if not isinstance(routing.get("priority"), int) or not 1 <= routing["priority"] <= 50000:
            raise AppYamlError("routing.priority is required: a unique integer 1-50000")
        normalize_login(routing)
        if "adopt_dns" in routing and not isinstance(routing["adopt_dns"], bool):
            raise AppYamlError("routing.adopt_dns must be true or false")
        if routing.get("adopt_dns"):
            _check_adoption(app["team"], "dns", host, "routing.adopt_dns")

    for rtype, items in (app.get("resources") or {}).items():
        if not isinstance(items, list):
            raise AppYamlError(f"resources.{rtype}: expected a list")
        for i, item in enumerate(items):
            _check_keys(f"resources.{rtype}[{i}]", item, RESOURCE_SCHEMA[rtype])
            if not RESOURCE_NAME_RE.match(str(item.get("name", ""))):
                raise AppYamlError(f"resources.{rtype}[{i}].name: {item.get('name')!r} is not a valid name")
            if item.get("env") and not ENV_RE.match(item["env"]):
                raise AppYamlError(f"resources.{rtype}[{i}].env: {item['env']!r} is not a valid variable name")
            if rtype == "databases":
                _validate_database(f"resources.databases[{i}]", item, app["team"])

    env = app.get("environment")
    if env is not None:
        if not isinstance(env, dict):
            raise AppYamlError("environment: expected a mapping of NAME: value")
        for k, v in env.items():
            if not ENV_RE.match(str(k)):
                raise AppYamlError(f"environment: {k!r} is not a valid variable name")
            if isinstance(v, (dict, list)):
                raise AppYamlError(f"environment.{k}: values must be strings, numbers or booleans")

    parse_peers(app)


def _scope_list(section, value, allowed=None):
    """A list of unique scope names; each must be in `allowed` when given."""
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(s, str) for s in value):
        raise AppYamlError(f"{section}: expected a list of scope names")
    for s in value:
        if not SCOPE_RE.match(s):
            raise AppYamlError(
                f"{section}: {s!r} is not a valid scope name "
                "(lowercase letters, digits, '.', '_' and '-', max 40 chars)"
            )
        if allowed is not None and s not in allowed:
            raise AppYamlError(f"{section}: {s!r} is not listed in provides.scopes")
    if len(set(value)) != len(value):
        raise AppYamlError(f"{section}: duplicate scope names")
    return list(value)


def _peer_ref(section, item, own_team):
    """Resolve {app, team?} to (team, app)."""
    peer_app, peer_team = item.get("app"), item.get("team", own_team)
    if not NAME_RE.match(str(peer_app or "")):
        raise AppYamlError(f"{section}.app: {peer_app!r} must be an app name (lowercase letters, digits, hyphens)")
    if not TEAM_RE.match(str(peer_team or "")):
        raise AppYamlError(f"{section}.team: {peer_team!r} must be a team name (lowercase letters)")
    return peer_team, peer_app


def peer_env_names(peer_app, env=None):
    """(URL variable, scopes variable) for a call."""
    url_env = env or f"{peer_app.upper().replace('-', '_')}_URL"
    scopes_env = url_env[:-len("_URL")] + "_SCOPES" if url_env.endswith("_URL") else f"{url_env}_SCOPES"
    return url_env, scopes_env


def parse_peers(app):
    """Validate and normalise `calls:` and `provides:`.

    Returns (calls, provides_scopes, consumers):
      calls:           [{team, app, key: "team/app", scopes, url_env, scopes_env}]
      provides_scopes: {scope: description}
      consumers:       {"team/app": [scope, ...]}
    """
    own = (app.get("team"), app.get("name"))

    calls, seen = [], set()
    raw_calls = app.get("calls")
    if raw_calls is not None:
        if not isinstance(raw_calls, list):
            raise AppYamlError("calls: expected a list of {app, team, scopes, env}")
        if len(raw_calls) > MAX_CALLS:
            raise AppYamlError(f"calls: at most {MAX_CALLS} entries")
        for i, item in enumerate(raw_calls):
            section = f"calls[{i}]"
            _check_keys(section, item, CALL_SCHEMA)
            ref = _peer_ref(section, item, own[0])
            if ref == own:
                raise AppYamlError(f"{section}: an app cannot call itself")
            if ref in seen:
                raise AppYamlError(f"{section}: {ref[0]}/{ref[1]} is listed twice")
            seen.add(ref)
            if item.get("env") is not None and not ENV_RE.match(str(item["env"])):
                raise AppYamlError(f"{section}.env: {item['env']!r} is not a valid variable name")
            url_env, scopes_env = peer_env_names(ref[1], item.get("env"))
            calls.append({
                "team": ref[0], "app": ref[1], "key": f"{ref[0]}/{ref[1]}",
                "scopes": _scope_list(f"{section}.scopes", item.get("scopes")),
                "url_env": url_env, "scopes_env": scopes_env,
            })

    provides = app.get("provides")
    provides_scopes, consumers = {}, {}
    if provides is not None:
        _check_keys("provides", provides, SCHEMA["provides"])
        raw_scopes = provides.get("scopes")
        if isinstance(raw_scopes, list):
            raw_scopes = {s: s for s in _scope_list("provides.scopes", raw_scopes)}
        if raw_scopes is not None:
            if not isinstance(raw_scopes, dict):
                raise AppYamlError("provides.scopes: expected a mapping of scope: description (or a list of names)")
            if len(raw_scopes) > MAX_SCOPES:
                raise AppYamlError(f"provides.scopes: at most {MAX_SCOPES} scopes")
            for s, desc in raw_scopes.items():
                _scope_list("provides.scopes", [s])
                if not isinstance(desc, str) or not 1 <= len(desc) <= 256:
                    raise AppYamlError(f"provides.scopes.{s}: description must be a string of 1-256 characters")
                provides_scopes[s] = desc

        raw_consumers = provides.get("consumers") or []
        if not isinstance(raw_consumers, list):
            raise AppYamlError("provides.consumers: expected a list of {app, team, scopes}")
        if len(raw_consumers) > MAX_CONSUMERS:
            raise AppYamlError(f"provides.consumers: at most {MAX_CONSUMERS} entries")
        for i, item in enumerate(raw_consumers):
            section = f"provides.consumers[{i}]"
            _check_keys(section, item, CONSUMER_SCHEMA)
            ref = _peer_ref(section, item, own[0])
            if ref == own:
                raise AppYamlError(f"{section}: an app cannot consume itself")
            key = f"{ref[0]}/{ref[1]}"
            if key in consumers:
                raise AppYamlError(f"{section}: {key} is listed twice")
            consumers[key] = _scope_list(f"{section}.scopes", item.get("scopes"), allowed=provides_scopes)

    return calls, provides_scopes, consumers


# ---------------------------------------------------------------------------
# HCL generation helpers
# ---------------------------------------------------------------------------

class Ref(str):
    """A Terraform expression (module.x.y, [module.a], data.z) — emitted unquoted."""


def _safe_key(k):
    """Make a string safe for use as an HCL map key (quote if needed)."""
    if re.match(r"^[A-Za-z_][A-Za-z0-9_-]*$", k):
        return k
    return hcl_string(k)


def hcl_string(s):
    """Quote a string literal, escaping quotes and template sequences."""
    s = s.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")
    s = s.replace("${", "$${").replace("%{", "%%{")
    return f'"{s}"'


def hcl_value(v, indent=2):
    """Format a Python value as HCL. Maps render multi-line at `indent`."""
    if isinstance(v, Ref):
        return str(v)
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        return hcl_string(v)
    if isinstance(v, list):
        return "[" + ", ".join(hcl_value(i, indent) for i in v) + "]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        inner = attr_lines({_safe_key(k): val for k, val in v.items()}, indent + 2)
        return "{\n" + "\n".join(inner) + "\n" + " " * indent + "}"
    raise TypeError(f"cannot render {type(v).__name__} as HCL")


def attr_lines(attrs, indent):
    """Render `key = value` lines aligned the way `terraform fmt` aligns them.

    fmt aligns `=` within runs of consecutive single-line attributes; a
    multi-line value ends the run and keeps a single space before `=`.
    """
    rendered = [(k, hcl_value(v, indent)) for k, v in attrs.items()]
    lines, run = [], []

    def flush():
        width = max((len(k) for k, _ in run), default=0)
        lines.extend(f"{' ' * indent}{k.ljust(width)} = {val}" for k, val in run)
        run.clear()

    for k, val in rendered:
        if "\n" in val:
            flush()
            lines.append(f"{' ' * indent}{k} = {val}")
        else:
            run.append((k, val))
    flush()
    return lines


def module_block(name, source, attrs):
    """Generate a module {} block."""
    lines = [f'module "{name}" {{', f"  source = {hcl_string(source)}", ""]
    lines.extend(attr_lines(attrs, 2))
    lines.append("}")
    return "\n".join(lines)


def import_block(to, import_id):
    """An `import {}` block adopting an existing resource. Once the resource is
    in state Terraform skips the block, so it can stay in the generated file."""
    return f"import {{\n  to = {to}\n  id = {hcl_string(import_id)}\n}}"


def _wire_postgres_env(env_map, mod_name, db):
    for key, output in (("env", "endpoint"), ("host_env", "address"), ("port_env", "port")):
        if db.get(key):
            env_map[db[key]] = Ref(f"module.{mod_name}.{output}")


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------

def _check_auth_urls(key, urls, allowed_hosts):
    """Callback/logout URLs: https (http only for localhost), no fragment, and a
    host the app serves, so a typo fails here instead of as redirect_mismatch."""
    if not isinstance(urls, list) or not all(isinstance(u, str) and u for u in urls):
        raise AppYamlError(f"auth.{key} must be a list of URLs")
    for u in urls:
        parts = urlsplit(u)
        host = (parts.hostname or "").lower()
        local = host in LOCAL_HOSTS
        if parts.scheme not in ("https", "http") or not host:
            raise AppYamlError(f"auth.{key}: {u!r} is not an absolute https:// URL")
        if parts.scheme == "http" and not local:
            raise AppYamlError(f"auth.{key}: {u!r} must use https:// (plain http is only allowed for localhost)")
        if "#" in u:
            raise AppYamlError(f"auth.{key}: {u!r} must not contain a #fragment (OAuth forbids it)")
        if not local and host not in allowed_hosts:
            where = f"routing.host ({', '.join(sorted(allowed_hosts))})" if allowed_hosts else "routing.host"
            raise AppYamlError(
                f"auth.{key}: host {host!r} in {u!r} is not this app's {where}; "
                f"list other hosts the app serves in auth.extra_hosts"
            )


def resolve_auth(app, routing=None):
    """The app's validated auth settings with defaults applied, or None.

    {pool, client, callback_urls, logout_urls, scopes, scopes_set, groups,
    aliases}. scripts/auth-doctor.py uses this to know what the request
    parameter should contain.
    """
    routing = routing if routing is not None else dict(app.get("routing") or {})
    if app.get("domain") and not routing.get("host"):
        routing = {**routing, "host": app["domain"]}
    auth_raw = app.get("auth")
    if not auth_raw or auth_raw == "none":
        return None
    # Accept shorthand: auth: internal -> {pool: internal}
    auth = {"pool": auth_raw} if isinstance(auth_raw, str) else auth_raw
    pool = auth.get("pool", "internal")
    if pool == "both":
        raise AppYamlError(
            "auth.pool: 'both' is not supported yet — pick 'internal' or 'external'."
        )
    if pool not in ("internal", "external"):
        raise AppYamlError(f"auth.pool must be 'internal' or 'external' (got {pool!r})")

    client = auth.get("client", "confidential")
    if client not in AUTH_CLIENT_TYPES:
        raise AppYamlError(f"auth.client must be one of {', '.join(AUTH_CLIENT_TYPES)} (got {client!r})")

    groups = auth.get("groups") or []
    if not isinstance(groups, list) or not all(isinstance(g, str) and g for g in groups):
        raise AppYamlError("auth.groups must be a list of group names")

    aliases = auth.get("env") or {}
    if not isinstance(aliases, dict):
        raise AppYamlError("auth.env must map injected names to extra variable names")

    if client == "none":
        # Validate-only apps get no client; these settings would be silently ignored
        ignored = [k for k in ("callback_urls", "logout_urls", "scopes", "extra_hosts") if k in auth]
        if ignored:
            raise AppYamlError(
                f"auth.client: none requests no Cognito client, so {', '.join(ignored)} would be ignored — "
                f"remove them, or use client: public / confidential"
            )
        return {"pool": pool, "client": client, "callback_urls": [], "logout_urls": [], "scopes": [],
                "scopes_set": False, "groups": groups, "aliases": aliases}

    extra_hosts = auth.get("extra_hosts") or []
    if not isinstance(extra_hosts, list) or not all(isinstance(h, str) and HOSTNAME_RE.match(h) for h in extra_hosts):
        raise AppYamlError("auth.extra_hosts must be a list of hostnames (e.g. [www.javazone.no]), no scheme or path")

    host = routing.get("host")
    if not host and not auth.get("callback_urls"):
        raise AppYamlError("auth: requires routing.host or auth.callback_urls (callback URLs default to the host)")
    allowed_hosts = {h.lower() for h in [host, *extra_hosts] if h}
    callback_urls = auth.get("callback_urls") or [f"https://{host}/", f"https://{host}/auth/callback"]
    logout_urls = auth.get("logout_urls") or ([f"https://{host}/"] if host else [])
    _check_auth_urls("callback_urls", callback_urls, allowed_hosts)
    _check_auth_urls("logout_urls", logout_urls, allowed_hosts)

    scopes = auth.get("scopes")
    return {"pool": pool, "client": client, "callback_urls": callback_urls, "logout_urls": logout_urls,
            "scopes": scopes or ["openid", "email", "profile"], "scopes_set": bool(scopes),
            "groups": groups, "aliases": aliases}


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def generate(app, account_id, region, platform_ref):
    """Generate all .tf file contents from app.yaml data."""
    validate(app)

    name = app["name"]
    team = app["team"]
    repo = os.environ.get("GITHUB_REPOSITORY", f"javaBin/{name}")

    compute = app.get("compute") or {}
    routing = dict(app.get("routing") or {})
    resources = app.get("resources") or {}
    alarms_cfg = app.get("alarms") or {}

    # `domain:` is shorthand for routing.host; routing.host wins.
    if app.get("domain") and not routing.get("host"):
        if not routing.get("priority"):
            raise AppYamlError("domain: requires routing.priority (unique 1-50000) to create the ALB rule")
        routing["host"] = app["domain"]

    imports = []  # (resource address, import id) for existing resources to adopt

    def src(mod):
        path = MODULE_SOURCES[mod]
        return f"git::https://github.com/javaBin/platform//{path}?ref={platform_ref}"

    files = {}

    # -- backend.tf --
    files["backend.tf"] = BACKEND_TEMPLATE.format(
        project=PROJECT, account_id=account_id, team=team,
        service=name, region=region,
    )

    # -- providers.tf --
    files["providers.tf"] = PROVIDERS_TEMPLATE.format(
        region=region, team=team, service=name, repo=repo,
    )

    # -- .gitignore --
    files[".gitignore"] = GITIGNORE_TEMPLATE

    # -- main.tf --
    blocks = []

    # Platform data (always)
    blocks.append(module_block("platform", src("platform-data"), {
        "project": PROJECT,
        "domain": DOMAIN,
        "team": team,
    }))

    # ECR (always)
    blocks.append(module_block("ecr", src("ecr-repo"), {
        "name": name,
        "team": team,
    }))

    # Routing (if host specified)
    if routing.get("host"):
        routing_attrs = {
            "name": name,
            "team": team,
            "vpc_id": Ref("module.platform.vpc_id"),
            "port": compute.get("port", 8000),
            "health_check_path": compute.get("health_check", "/health"),
            "health_check_matcher": str(compute.get("health_check_matcher", "200")),
            "https_listener_arn": Ref("module.platform.https_listener_arn"),
            "listener_rule_priority": routing["priority"],
            "host_header": routing["host"],
            "route53_zone_id": Ref(f'module.platform.route53_zone_ids["{host_zone(routing["host"])}"]'),
            "alb_dns_name": Ref("module.platform.alb_dns_name"),
            "alb_zone_id": Ref("module.platform.alb_zone_id"),
            "deregistration_delay": routing.get("deregistration_delay", 30),
            # adopt_dns: take over an existing record (Route53 UPSERT). Only
            # emitted when set, so other apps' generated files don't change.
            **({"allow_dns_overwrite": True} if routing.get("adopt_dns") else {}),
        }
        # routing.login: one ALB rule per entry, at the priorities directly
        # below routing.priority so they are evaluated before the host rule.
        login = normalize_login(routing)
        if login:
            routing_attrs["project"] = PROJECT
            routing_attrs["login_rules"] = {
                f"p{i + 1}": {
                    "groups": rule["groups"],
                    "paths": rule["paths"],
                    "pool": rule["pool"],
                    "priority": routing["priority"] - 1 - i,
                    "public": rule["public"],
                }
                for i, rule in enumerate(login)
            }
            for warning in login_warnings(login):
                print(f"::warning file=app.yaml::{warning}")
        blocks.append(module_block("routing", src("service-routing"), routing_attrs))

    # Per-app network: own security group, Cloud Map registration and the
    # app-to-app request read by the platform app-peers-sync Lambda (always).
    calls, provides_scopes, consumers = parse_peers(app)
    network_attrs = {
        "name": name,
        "team": team,
        "project": PROJECT,
        "vpc_id": Ref("module.platform.vpc_id"),
        "port": compute.get("port", 8000),
        "alb_ingress": bool(routing.get("host")),
    }
    if calls:
        network_attrs["calls"] = {c["key"]: c["scopes"] for c in calls}
    if provides_scopes:
        network_attrs["provides_scopes"] = provides_scopes
    if consumers:
        network_attrs["consumers"] = consumers
    blocks.append(module_block("network", src("app-network"), network_attrs))

    # Collection modules (buckets, databases, secrets, queues)
    policy_map = {}  # name -> module output ref for access_policy_json
    env_map = {}     # env var name -> module output ref
    secret_map = {}  # env var name -> module output ref

    for bucket in resources.get("buckets", []):
        bname = bucket["name"]
        mod_name = f"bucket_{bname}"
        blocks.append(module_block(mod_name, src("service-bucket"), {
            "name": bname,
            "team": team,
            "aws_account_id": account_id,
            "service": name,
            "versioning": bucket.get("versioning", True),
            "expire_days": bucket.get("expire_days", 0),
        }))
        policy_map[mod_name] = Ref(f"module.{mod_name}.access_policy_json")
        if bucket.get("env"):
            env_map[bucket["env"]] = Ref(f"module.{mod_name}.bucket_name")

    for db in resources.get("databases", []):
        dname = db["name"]
        engine = (db.get("engine") or "dynamodb").lower()
        if engine in POSTGRES_ENGINES and db.get("adopt"):
            # Existing instance: imported, never created. Password auth with
            # the app's own credentials, so no task-role policy.
            mod_name = f"rds_{dname}"
            attrs = {
                "identifier": db["adopt"],
                "deletion_protection": db.get("deletion_protection", True),
            }
            if db.get("backup_retention_period") is not None:
                attrs["backup_retention_period"] = db["backup_retention_period"]
            blocks.append(module_block(mod_name, src("service-rds-adopted"), attrs))
            imports.append((f"module.{mod_name}.aws_db_instance.this", db["adopt"]))
            _wire_postgres_env(env_map, mod_name, db)
        elif engine in POSTGRES_ENGINES:
            mod_name = f"rds_{dname}"
            blocks.append(module_block(mod_name, src("service-rds"), {
                "name": dname,
                "team": team,
                "project": PROJECT,
                "engine_version": str(db.get("engine_version", "16")),
                "instance_class": db.get("instance_class", "db.t3.micro"),
                "allocated_storage": db.get("allocated_storage", 20),
                "subnet_ids": Ref("module.platform.private_subnet_ids"),
                "vpc_id": Ref("module.platform.vpc_id"),
                "allowed_security_group_ids": Ref("[module.network.security_group_id]"),
                "backup_retention_period": db.get("backup_retention_period", 7),
                "multi_az": db.get("multi_az", False),
                "deletion_protection": db.get("deletion_protection", True),
            }))
            policy_map[mod_name] = Ref(f"module.{mod_name}.access_policy_json")
            _wire_postgres_env(env_map, mod_name, db)
        elif engine == "dynamodb":
            mod_name = f"database_{dname}"
            attrs = {
                "name": dname,
                "team": team,
                "service": name,
                "hash_key": db.get("hash_key", "id"),
                "hash_key_type": db.get("hash_key_type", "S"),
            }
            if db.get("range_key"):
                attrs["range_key"] = db["range_key"]
                attrs["range_key_type"] = db.get("range_key_type", "S")
            if db.get("ttl_attribute"):
                attrs["ttl_attribute"] = db["ttl_attribute"]
            blocks.append(module_block(mod_name, src("service-database"), attrs))
            policy_map[mod_name] = Ref(f"module.{mod_name}.access_policy_json")
            if db.get("env"):
                env_map[db["env"]] = Ref(f"module.{mod_name}.table_name")
        else:
            raise AppYamlError(f"resources.databases {dname}: engine must be dynamodb or postgres (got {engine!r})")

    for secret in resources.get("secrets", []):
        sname = secret["name"]
        mod_name = f"secret_{sname}"
        blocks.append(module_block(mod_name, src("service-secret"), {
            "name": sname,
            "team": team,
            "project": PROJECT,
            "service": name,
            "description": secret.get("description", ""),
        }))
        policy_map[mod_name] = Ref(f"module.{mod_name}.access_policy_json")
        if secret.get("env"):
            secret_map[secret["env"]] = Ref(f"module.{mod_name}.parameter_arn")

    for queue in resources.get("queues", []):
        qname = queue["name"]
        mod_name = f"queue_{qname}"
        blocks.append(module_block(mod_name, src("service-queue"), {
            "name": qname,
            "team": team,
            "service": name,
            "visibility_timeout_seconds": queue.get("visibility_timeout", 30),
            "retention_seconds": queue.get("retention_seconds", 345600),
            "max_receive_count": queue.get("max_receive_count", 3),
        }))
        policy_map[mod_name] = Ref(f"module.{mod_name}.access_policy_json")
        if queue.get("env"):
            env_map[queue["env"]] = Ref(f"module.{mod_name}.queue_url")

    # Auth (Cognito app client) — optional. The module only *requests* the
    # client; the platform app-auth-sync Lambda creates it (see module docs).
    auth = resolve_auth(app, routing)
    if auth:
        client = auth["client"]
        auth_attrs = {
            "app_name": name,
            "team": team,
            "pool": auth["pool"],
            "client_type": client,
            "project": PROJECT,
        }
        if client != "none":
            auth_attrs["callback_urls"] = auth["callback_urls"]
            auth_attrs["logout_urls"] = auth["logout_urls"]
            if auth["groups"]:
                auth_attrs["allowed_groups"] = auth["groups"]
            if auth["scopes_set"]:
                auth_attrs["allowed_oauth_scopes"] = auth["scopes"]

        blocks.append(module_block("auth", src("cognito-app-client"), auth_attrs))

        auth_env = {
            "COGNITO_USER_POOL_ID": Ref("module.auth.user_pool_id"),
            "COGNITO_DOMAIN": Ref("module.auth.user_pool_domain"),
            "COGNITO_ISSUER_URL": Ref("module.auth.issuer_url"),
            "COGNITO_WELL_KNOWN_URL": Ref("module.auth.well_known_url"),
        }
        if auth["groups"]:
            auth_env["COGNITO_GROUPS"] = ",".join(auth["groups"])
        auth_secrets = {}
        if client != "none":
            auth_secrets["COGNITO_CLIENT_ID"] = Ref("module.auth.client_id_arn")
            # The IdP the client signs in through (written by app-auth-sync),
            # for apps that pass identity_provider themselves
            auth_secrets["COGNITO_IDENTITY_PROVIDER"] = Ref("module.auth.identity_provider_arn")
        if client == "confidential":
            auth_secrets["COGNITO_CLIENT_SECRET"] = Ref("module.auth.client_secret_arn")

        # auth.env: expose the same values under names the app already reads,
        # e.g. {COGNITO_CLIENT_ID: NUXT_PUBLIC_OIDC_CLIENT_ID}
        for source, alias in auth["aliases"].items():
            if source not in auth_env and source not in auth_secrets:
                available = ", ".join(sorted({*auth_env, *auth_secrets}))
                raise AppYamlError(f"auth.env: {source} is not injected for this app (available: {available})")
            if not ENV_RE.match(str(alias)):
                raise AppYamlError(f"auth.env.{source}: {alias!r} is not a valid variable name")
            (auth_env if source in auth_env else auth_secrets)[alias] = (auth_env | auth_secrets)[source]

        env_map.update(auth_env)
        secret_map.update(auth_secrets)

    # App-to-app env: the peer URL (a parameter, since the callee's port lives
    # in another team's state), the scopes actually granted (a parameter too:
    # Cognito refuses the whole token if one requested scope is not granted,
    # and a change restarts the caller through app-secret-rollout), and M2M creds.
    peer_env, peer_secrets = {}, {}
    peer_names = [n for c in calls for n in (c["url_env"], c["scopes_env"] if c["scopes"] else None) if n]
    for c in calls:
        peer_secrets[c["url_env"]] = Ref(f'module.network.peer_url_arns["{c["key"]}"]')
        if c["scopes"]:
            peer_secrets[c["scopes_env"]] = Ref(f'module.network.peer_scopes_arns["{c["key"]}"]')
    if any(c["scopes"] for c in calls):
        peer_env["PEER_AUTH_TOKEN_URL"] = Ref("module.network.token_url")
        peer_secrets["PEER_AUTH_CLIENT_ID"] = Ref("module.network.m2m_client_id_arn")
        peer_secrets["PEER_AUTH_CLIENT_SECRET"] = Ref("module.network.m2m_client_secret_arn")
    if provides_scopes:
        peer_env["PEER_AUTH_ISSUER"] = Ref("module.network.issuer_url")
        peer_env["PEER_AUTH_JWKS_URL"] = Ref("module.network.jwks_url")
        peer_env["PEER_AUTH_SCOPE_PREFIX"] = f"{team}-{name}/"

    if any(c["scopes"] for c in calls):
        peer_names += PEER_CALLER_AUTH_ENV
    if provides_scopes:
        peer_names += PEER_CALLEE_AUTH_ENV
    if len(set(peer_names)) != len(peer_names):
        dup = sorted({n for n in peer_names if peer_names.count(n) > 1})
        raise AppYamlError(f"calls: variable name(s) {', '.join(dup)} used twice — set env: on the calls")
    taken = {*(app.get("environment") or {}), *env_map, *secret_map}
    clash = sorted(set(peer_names) & taken)
    if clash:
        raise AppYamlError(
            f"calls/provides: {', '.join(clash)} is injected by the platform but also set by "
            "environment:, resources or auth — remove it there or set env: on the call"
        )
    env_map.update(peer_env)
    secret_map.update(peer_secrets)

    # Task role (always)
    blocks.append(module_block("task_role", src("service-role"), {
        "name": name,
        "team": team,
        "region": region,
        "aws_account_id": account_id,
        "permissions_boundary_arn": Ref("module.platform.developer_boundary_arn"),
        "additional_policy_jsons": policy_map,
    }))

    # Merge static env vars with resource-derived env vars. ECS env values
    # are strings, so YAML numbers/booleans are stringified.
    all_env = {}
    for k, v in (app.get("environment") or {}).items():
        all_env[k] = ("true" if v else "false") if isinstance(v, bool) else ("" if v is None else str(v))
    all_env.update(env_map)

    # ECS service (always)
    service_attrs = {
        "name": name,
        "team": team,
        "cluster_id": Ref("module.platform.ecs_cluster_id"),
        "image": Ref("module.ecr.repository_url"),
        "cpu": compute.get("cpu", 512),
        "memory": compute.get("memory", 1024),
        "port": compute.get("port", 8000),
        "desired_count": compute.get("desired_count", 1),
        "execution_role_arn": Ref("module.platform.execution_role_arn"),
        "task_role_arn": Ref("module.task_role.role_arn"),
        "subnet_ids": Ref("module.platform.private_subnet_ids"),
        "security_group_ids": Ref("[module.network.security_group_id]"),
        "service_registry_arns": Ref("[module.network.registry_arn]"),
        "region": region,
        "container_user": str(compute.get("user", "1000")),
        "readonly_root_filesystem": compute.get("readonly_root_filesystem", True),
    }
    if routing.get("host"):
        service_attrs["target_group_arn"] = Ref("module.routing.target_group_arn")
    if all_env:
        service_attrs["environment"] = all_env
    if secret_map:
        service_attrs["secrets"] = secret_map

    blocks.append(module_block("service", src("ecs-service"), service_attrs))

    # Alarms (conditional)
    if alarms_cfg.get("enabled", True) and routing.get("host"):
        blocks.append(module_block("alarms", src("service-alarm"), {
            "name": name,
            "team": team,
            "service": name,
            "cluster_name": Ref("module.platform.ecs_cluster_name"),
            "sns_topic_arns": Ref("[data.aws_sns_topic.alerts.arn]"),
            "cpu_threshold": alarms_cfg.get("cpu_threshold", 80),
            "memory_threshold": alarms_cfg.get("memory_threshold", 80),
            "error_5xx_threshold": alarms_cfg.get("error_5xx_threshold", 10),
            "target_group_arn_suffix": Ref("module.routing.target_group_arn_suffix"),
            "alb_arn_suffix": Ref("module.platform.alb_arn_suffix"),
        }))
        blocks.append(f"""data "aws_sns_topic" "alerts" {{
  name = "{PROJECT}-alerts"
}}""")

    blocks.extend(import_block(to, id_) for to, id_ in imports)

    files["main.tf"] = GENERATED + "\n\n".join(blocks) + "\n"

    # -- outputs.tf --
    service_url = f"https://{routing['host']}" if routing.get("host") else ""
    files["outputs.tf"] = OUTPUTS_TEMPLATE.format(service_url=service_url)

    return files


def main():
    service = os.environ["APP_SERVICE"]
    account_id = os.environ["AWS_ACCOUNT_ID"]
    region = os.environ.get("AWS_REGION", "eu-central-1")
    tf_root = os.environ.get("TF_ROOT", "terraform")
    platform_ref = os.environ.get("PLATFORM_REF", "main")

    app_yaml_path = os.path.join(os.environ.get("GITHUB_WORKSPACE", "."), "app.yaml")
    if not os.path.exists(app_yaml_path):
        print(f"No app.yaml found at {app_yaml_path}")
        sys.exit(1)

    app = load_yaml(app_yaml_path) or {}
    if isinstance(app, dict) and not app.get("name"):
        app["name"] = service

    try:
        files = generate(app, account_id, region, platform_ref)
    except AppYamlError as e:
        # GitHub annotation so the error shows on the run summary
        print(f"::error file=app.yaml::{e}")
        print(f"ERROR: app.yaml: {e}", file=sys.stderr)
        sys.exit(1)

    os.makedirs(tf_root, exist_ok=True)
    for filename, content in files.items():
        path = os.path.join(tf_root, filename)
        with open(path, "w") as f:
            f.write(content)
        print(f"  wrote {path}")

    print(f"Generated {len(files)} files in {tf_root}/")


if __name__ == "__main__":
    main()
