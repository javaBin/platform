#!/usr/bin/env python3
"""Generate Terraform module blocks from app.yaml.

Reads app.yaml and produces .tf files with module {} blocks. Terraform handles
variable resolution, cross-module references, and output wiring natively.

Usage:
  python3 generate-modules.py

Required env vars: APP_SERVICE, AWS_ACCOUNT_ID, AWS_REGION, TF_ROOT
Optional env vars: PLATFORM_REF (git ref for module source, default: main)
"""

import json
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from registry import (
    PROJECT, DOMAIN, MODULE_SOURCES,
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


def yaml_get(data, dot_path, default=None):
    keys = dot_path.split(".")
    val = data
    for k in keys:
        if isinstance(val, dict) and k in val:
            val = val[k]
        else:
            return default
    return val


# ---------------------------------------------------------------------------
# HCL generation helpers
# ---------------------------------------------------------------------------

def _safe_key(k):
    """Make a string safe for use as an HCL map key (quote if needed)."""
    if all(c.isalnum() or c == "_" for c in k):
        return k
    return f'"{k}"'


def hcl_value(v):
    """Format a Python value as HCL."""
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return str(v)
    if isinstance(v, str):
        if v.startswith("module.") or v.startswith("data.") or v.startswith("["):
            return v  # reference or list — no quoting
        return f'"{v}"'
    if isinstance(v, list):
        items = ", ".join(hcl_value(i) for i in v)
        return f"[{items}]"
    if isinstance(v, dict):
        if not v:
            return "{}"
        lines = []
        for k, val in v.items():
            lines.append(f'    {_safe_key(k)} = {hcl_value(val)}')
        return "{\n" + "\n".join(lines) + "\n  }"
    return str(v)


def module_block(name, source, attrs):
    """Generate a module {} block."""
    lines = [f'module "{name}" {{', f'  source = "{source}"', ""]
    for k, v in attrs.items():
        lines.append(f"  {k:30s} = {hcl_value(v)}")
    lines.append("}")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main generation
# ---------------------------------------------------------------------------

def generate(app, account_id, region, platform_ref):
    """Generate all .tf file contents from app.yaml data."""
    name = app["name"]
    team = app["team"]
    full_name = f"{team}-{name}"
    repo = os.environ.get("GITHUB_REPOSITORY", f"javaBin/{name}")

    compute = app.get("compute", {})
    routing = app.get("routing", {})
    resources = app.get("resources", {})
    alarms_cfg = app.get("alarms", {})

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
    }))

    # ECR (always)
    blocks.append(module_block("ecr", src("ecr-repo"), {
        "name": name,
        "team": team,
    }))

    # Routing (if host specified)
    if routing.get("host"):
        blocks.append(module_block("routing", src("service-routing"), {
            "name": name,
            "team": team,
            "vpc_id": "module.platform.vpc_id",
            "port": compute.get("port", 8000),
            "health_check_path": compute.get("health_check", "/health"),
            "health_check_matcher": compute.get("health_check_matcher", "200"),
            "https_listener_arn": "module.platform.https_listener_arn",
            "listener_rule_priority": routing["priority"],
            "host_header": routing["host"],
            "route53_zone_id": "module.platform.route53_zone_id",
            "alb_dns_name": "module.platform.alb_dns_name",
            "alb_zone_id": "module.platform.alb_zone_id",
            "deregistration_delay": routing.get("deregistration_delay", 30),
        }))

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
        policy_map[mod_name] = f"module.{mod_name}.access_policy_json"
        if bucket.get("env"):
            env_map[bucket["env"]] = f"module.{mod_name}.bucket_name"

    for db in resources.get("databases", []):
        dname = db["name"]
        engine = (db.get("engine") or "dynamodb").lower()
        if engine in ("postgres", "postgresql"):
            mod_name = f"rds_{dname}"
            blocks.append(module_block(mod_name, src("service-rds"), {
                "name": dname,
                "team": team,
                "project": PROJECT,
                "engine_version": db.get("engine_version", "16"),
                "instance_class": db.get("instance_class", "db.t3.micro"),
                "allocated_storage": db.get("allocated_storage", 20),
                "subnet_ids": "module.platform.private_subnet_ids",
                "vpc_id": "module.platform.vpc_id",
                "allowed_security_group_ids": "[module.platform.ecs_tasks_security_group_id]",
                "backup_retention_period": db.get("backup_retention_period", 7),
                "multi_az": db.get("multi_az", False),
                "deletion_protection": db.get("deletion_protection", True),
            }))
            policy_map[mod_name] = f"module.{mod_name}.access_policy_json"
            if db.get("env"):
                env_map[db["env"]] = f"module.{mod_name}.endpoint"
        else:
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
            policy_map[mod_name] = f"module.{mod_name}.access_policy_json"
            if db.get("env"):
                env_map[db["env"]] = f"module.{mod_name}.table_name"

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
        policy_map[mod_name] = f"module.{mod_name}.access_policy_json"
        if secret.get("env"):
            secret_map[secret["env"]] = f"module.{mod_name}.parameter_arn"

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
        policy_map[mod_name] = f"module.{mod_name}.access_policy_json"
        if queue.get("env"):
            env_map[queue["env"]] = f"module.{mod_name}.queue_url"

    # Auth (Cognito app client) — optional
    auth_raw = app.get("auth")
    if auth_raw and auth_raw != "none":
        # Accept shorthand: auth: internal -> {pool: internal}
        auth = {"pool": auth_raw} if isinstance(auth_raw, str) else auth_raw
        pool = auth.get("pool")
        if pool == "both":
            raise NotImplementedError(
                "auth.pool: 'both' is not yet supported — pick 'internal' or 'external'. "
                "When the first concrete need lands, decide on COGNITO_INTERNAL_*/COGNITO_EXTERNAL_* env-var conventions."
            )
        if pool not in ("internal", "external"):
            raise ValueError(f"auth.pool must be 'internal' or 'external' (got {pool!r})")

        host = routing.get("host")
        if not host:
            raise ValueError("auth: requires routing.host (callback URLs default to it)")

        callback_urls = auth.get("callback_urls") or [
            f"https://{host}/",
            f"https://{host}/auth/callback",
        ]
        logout_urls = auth.get("logout_urls") or [f"https://{host}/"]

        auth_attrs = {
            "app_name": name,
            "team": team,
            "pool": pool,
            "callback_urls": callback_urls,
            "logout_urls": logout_urls,
            "project": PROJECT,
        }
        if auth.get("scopes"):
            auth_attrs["allowed_oauth_scopes"] = auth["scopes"]
        if auth.get("external_pool_custom_domain"):
            auth_attrs["external_pool_custom_domain"] = auth["external_pool_custom_domain"]

        blocks.append(module_block("auth", src("cognito-app-client"), auth_attrs))
        policy_map["auth"] = "module.auth.access_policy_json"

        env_map["COGNITO_USER_POOL_ID"] = f"module.auth.{pool}_user_pool_id"
        env_map["COGNITO_DOMAIN"] = f"module.auth.{pool}_user_pool_domain"
        env_map["COGNITO_ISSUER_URL"] = f"module.auth.{pool}_issuer_url"
        secret_map["COGNITO_CLIENT_ID"] = f"module.auth.{pool}_client_id_arn"
        secret_map["COGNITO_CLIENT_SECRET"] = f"module.auth.{pool}_client_secret_arn"

        if auth.get("groups"):
            env_map["COGNITO_GROUPS"] = ",".join(auth["groups"])

    # Task role (always)
    blocks.append(module_block("task_role", src("service-role"), {
        "name": name,
        "team": team,
        "region": region,
        "aws_account_id": account_id,
        "permissions_boundary_arn": "module.platform.developer_boundary_arn",
        "additional_policy_jsons": policy_map if policy_map else {},
    }))

    # Merge static env vars with resource-derived env vars
    static_env = app.get("environment", {}) or {}
    all_env = {**static_env}
    for k, v in env_map.items():
        all_env[k] = v

    # ECS service (always)
    service_attrs = {
        "name": name,
        "team": team,
        "cluster_id": "module.platform.ecs_cluster_id",
        "image": 'module.ecr.repository_url',
        "cpu": compute.get("cpu", 512),
        "memory": compute.get("memory", 1024),
        "port": compute.get("port", 8000),
        "desired_count": compute.get("desired_count", 1),
        "execution_role_arn": "module.platform.execution_role_arn",
        "task_role_arn": "module.task_role.role_arn",
        "subnet_ids": "module.platform.private_subnet_ids",
        "security_group_ids": "[module.platform.ecs_tasks_security_group_id]",
        "region": region,
        "container_user": str(compute.get("user", "1000")),
    }
    if routing.get("host"):
        service_attrs["target_group_arn"] = "module.routing.target_group_arn"
    if all_env:
        service_attrs["environment"] = all_env
    if secret_map:
        service_attrs["secrets"] = secret_map

    blocks.append(module_block("service", src("ecs-service"), service_attrs))

    # Alarms (conditional)
    alarms_enabled = alarms_cfg.get("enabled", True) if alarms_cfg else True
    if alarms_enabled and routing.get("host"):
        blocks.append(module_block("alarms", src("service-alarm"), {
            "name": name,
            "team": team,
            "service": name,
            "cluster_name": "module.platform.ecs_cluster_name",
            "sns_topic_arns": f'[data.aws_sns_topic.alerts.arn]',
            "cpu_threshold": alarms_cfg.get("cpu_threshold", 80),
            "memory_threshold": alarms_cfg.get("memory_threshold", 80),
            "error_5xx_threshold": alarms_cfg.get("error_5xx_threshold", 10),
            "target_group_arn_suffix": "module.routing.target_group_arn_suffix",
            "alb_arn_suffix": "module.platform.alb_arn_suffix",
        }))
        # Add the data source for SNS topic
        blocks.append(f"""
data "aws_sns_topic" "alerts" {{
  name = "{PROJECT}-alerts"
}}""")

    files["main.tf"] = GENERATED + "\n\n".join(blocks) + "\n"

    # -- outputs.tf --
    host = routing.get("host", f"{name}.{DOMAIN}")
    files["outputs.tf"] = OUTPUTS_TEMPLATE.format(host=host)

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

    app = load_yaml(app_yaml_path)
    if not app.get("name"):
        app["name"] = service
    if not app.get("team"):
        print("ERROR: app.yaml must have a 'team' field")
        sys.exit(1)

    files = generate(app, account_id, region, platform_ref)

    os.makedirs(tf_root, exist_ok=True)
    for filename, content in files.items():
        path = os.path.join(tf_root, filename)
        with open(path, "w") as f:
            f.write(content)
        print(f"  wrote {path}")

    print(f"Generated {len(files)} files in {tf_root}/")


if __name__ == "__main__":
    main()
