#!/usr/bin/env python3
"""Terraform module expander — generates raw resource definitions from app.yaml.

Reads the module registry (registry.py) and module source files, then expands
them into concrete .tf files with actual aws_* resource definitions.

The registry is the ONLY place that knows about modules. This script is
module-agnostic — it reads module .tf files from disk and performs:
  1. Variable substitution (var.xxx -> concrete values)
  2. Resource renaming (.this -> .routing, .bucket_uploads, etc.)
  3. Internal reference rewriting (resource_type.this -> resource_type.renamed)
  4. Cross-module reference resolution (via output_map)
  5. Construct resolution (count, dynamic, for_each)

Usage:
  python3 expand-modules.py

Required env vars: APP_SERVICE, AWS_ACCOUNT_ID, AWS_REGION, TF_ROOT
Optional env vars: PLATFORM_ROOT (defaults to .platform)
"""

import json
import os
import re
import subprocess
import sys

# Import registry from the same directory
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from registry import (
    REGISTRY, PROJECT, DOMAIN,
    BACKEND_TEMPLATE, PROVIDERS_TEMPLATE, OUTPUTS_TEMPLATE,
)

GENERATED_MARKER = "# GENERATED FROM app.yaml — do not edit, changes will be overwritten"


# ---------------------------------------------------------------------------
# YAML helpers
# ---------------------------------------------------------------------------

def load_yaml(path):
    """Load YAML via yq (converts to JSON) — no pyyaml dependency."""
    result = subprocess.run(
        ["yq", "-o", "json", path],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        # Fallback: try python yaml module if available
        try:
            import yaml
            with open(path) as f:
                return yaml.safe_load(f)
        except ImportError:
            raise RuntimeError(f"Failed to parse {path}: yq not found and pyyaml not installed")
    return json.loads(result.stdout)


def yaml_get(data, dot_path, default=None):
    """Traverse a nested dict by dot-separated path."""
    keys = dot_path.split(".")
    current = data
    for key in keys:
        if not isinstance(current, dict) or key not in current:
            return default
        current = current[key]
    return current


# ---------------------------------------------------------------------------
# Expression resolver (the mini DSL)
# ---------------------------------------------------------------------------

def resolve_expr(expr, yaml_data, env_vars, item=None, ref_resolver=None):
    """Resolve a registry DSL expression to a concrete value.

    Returns (value, is_reference) where is_reference=True means the value
    is a Terraform expression (not a quoted string).
    """
    if expr.startswith("yaml:"):
        return _resolve_yaml(expr[5:], yaml_data)
    if expr.startswith("const:"):
        val = expr[6:]
        if val in ("true", "false"):
            return val, True  # bare boolean
        if val == "null":
            return "null", True
        try:
            float(val)
            return val, True  # bare number
        except ValueError:
            return val, False  # string
    if expr.startswith("env:"):
        return env_vars.get(expr[4:], ""), False
    if expr.startswith("item:"):
        return _resolve_item(expr[5:], item)
    if expr.startswith("ref:"):
        ref = expr[4:]
        if ref_resolver:
            resolved = ref_resolver(ref)
            return resolved, True  # always a TF reference
        return f"UNRESOLVED:{ref}", True
    if expr.startswith("list:"):
        inner = expr[5:]
        # Check if it's a raw TF reference (contains dots, not a DSL expression)
        if not any(inner.startswith(p) for p in ("yaml:", "const:", "env:", "item:", "ref:", "expr:")):
            # Bare TF reference like "data.aws_sns_topic.alerts.arn"
            return f"[{inner}]", True
        inner_val, is_ref = resolve_expr(inner, yaml_data, env_vars, item, ref_resolver)
        if is_ref:
            return f"[{inner_val}]", True
        return f'["{inner_val}"]', True
    if expr.startswith("expr:"):
        return _resolve_interpolation(expr[5:], yaml_data, env_vars, item, ref_resolver)
    if expr.startswith("collect:"):
        # Handled specially by the main loop
        return expr, True

    return expr, False


def _resolve_yaml(path_with_default, yaml_data):
    parts = path_with_default.split("|")
    path = parts[0]
    default = None
    for part in parts[1:]:
        if part.startswith("default:"):
            default = part[8:]

    val = yaml_get(yaml_data, path)
    if val is None:
        if default is not None:
            if default in ("true", "false"):
                return default, True
            if default == "null":
                return "null", True
            try:
                float(default)
                return default, True
            except ValueError:
                return default, False
        raise ValueError(f"Required YAML path '{path}' not found and no default")

    if isinstance(val, bool):
        return str(val).lower(), True
    if isinstance(val, (int, float)):
        return str(val), True
    return str(val), False


def _resolve_item(path_with_default, item):
    parts = path_with_default.split("|")
    field = parts[0]
    default = None
    for part in parts[1:]:
        if part.startswith("default:"):
            default = part[8:]

    val = item.get(field) if item else None
    if val is None:
        if default is not None:
            if default in ("true", "false"):
                return default, True
            if default == "null":
                return "null", True
            if default == "":
                return "", False
            try:
                float(default)
                return default, True
            except ValueError:
                return default, False
        raise ValueError(f"Required item field '{field}' not found")

    if isinstance(val, bool):
        return str(val).lower(), True
    if isinstance(val, (int, float)):
        return str(val), True
    return str(val), False


def _resolve_interpolation(template, yaml_data, env_vars, item, ref_resolver):
    """Resolve ${ref:xxx} and ${yaml:xxx} within an interpolation string.

    Example: "${ref:ecr.repository_url}:latest"
    → resolves ref → "aws_ecr_repository.ecr.repository_url"
    → produces: "${aws_ecr_repository.ecr.repository_url}:latest"
    """
    parts = []
    has_refs = False
    last_end = 0

    for match in re.finditer(r'\$\{([^}]+)\}', template):
        parts.append(template[last_end:match.start()])
        inner = match.group(1)
        val, is_ref = resolve_expr(inner, yaml_data, env_vars, item, ref_resolver)
        if is_ref:
            parts.append("${" + val + "}")
            has_refs = True
        else:
            parts.append(val)
        last_end = match.end()

    parts.append(template[last_end:])
    resolved = "".join(parts)

    if has_refs:
        return f'"{resolved}"', True
    return resolved, False


# ---------------------------------------------------------------------------
# Module source reader
# ---------------------------------------------------------------------------

def read_module_source(platform_root, module_path):
    """Read the main.tf content from a module directory."""
    main_tf = os.path.join(platform_root, module_path, "main.tf")
    if not os.path.exists(main_tf):
        raise FileNotFoundError(f"Module source not found: {main_tf}")
    with open(main_tf) as f:
        return f.read()


def read_module_variables(platform_root, module_path):
    """Read variable names, types, and defaults from variables.tf."""
    var_tf = os.path.join(platform_root, module_path, "variables.tf")
    if not os.path.exists(var_tf):
        return {}
    with open(var_tf) as f:
        content = f.read()

    variables = {}
    for match in re.finditer(
        r'variable\s+"(\w+)"\s*\{(.*?)\n\}',
        content, re.DOTALL
    ):
        name = match.group(1)
        body = match.group(2)
        var_type = "string"
        type_match = re.search(r'type\s*=\s*(\S+)', body)
        if type_match:
            var_type = type_match.group(1)

        default = None
        default_match = re.search(r'default\s*=\s*(.+)', body)
        if default_match:
            raw = default_match.group(1).strip()
            # Clean up trailing comments or braces
            raw = re.sub(r'\s*#.*$', '', raw)
            if raw.endswith('"'):
                # String default: extract between quotes
                str_match = re.search(r'"(.*)"', raw)
                if str_match:
                    default = str_match.group(1)
            elif raw in ("true", "false"):
                default = raw
            elif raw == "null":
                default = None
            else:
                try:
                    default = str(int(raw))
                except ValueError:
                    try:
                        default = str(float(raw))
                    except ValueError:
                        default = raw

        variables[name] = {"type": var_type, "default": default}
    return variables


# ---------------------------------------------------------------------------
# HCL expansion engine
# ---------------------------------------------------------------------------

def expand_hcl(source, var_values, rename_from, rename_to, extra_renames=None):
    """Expand a module's main.tf by substituting variables and renaming resources.

    Args:
        source: The raw HCL text from the module's main.tf
        var_values: Dict of {var_name: (value, is_reference)}
        rename_from: Original resource name suffix (usually "this")
        rename_to: New resource name suffix (e.g., "routing", "bucket_uploads")
        extra_renames: Additional name mappings for non-"this" resources (e.g., "dlq" -> "queue_x_dlq")
    """
    result = source

    # 1. Substitute var.xxx references
    for var_name, (value, is_ref) in var_values.items():
        # First handle "${var.xxx}" interpolation patterns (replace with just the value)
        result = re.sub(
            rf'"\$\{{var\.{re.escape(var_name)}\}}"',
            lambda m, v=value, r=is_ref: v if r else f'"{v}"',
            result,
        )
        # Then handle ${var.xxx} inside larger strings (inline substitution)
        result = re.sub(
            rf'\$\{{var\.{re.escape(var_name)}\}}',
            lambda m, v=value: str(v),
            result,
        )
        # Finally handle bare var.xxx references
        if is_ref:
            result = re.sub(
                rf'\bvar\.{re.escape(var_name)}\b',
                lambda m, v=value: v,
                result,
            )
        else:
            result = re.sub(
                rf'\bvar\.{re.escape(var_name)}\b',
                lambda m, v=value: f'"{v}"',
                result,
            )

    # 2. Rename resources and internal references
    # Find all resource/data types declared in the source
    declared = set()
    for match in re.finditer(r'(?:resource|data)\s+"(\w+)"\s+"(\w+)"', result):
        declared.add((match.group(1), match.group(2)))

    renames = extra_renames or {}
    renames[rename_from] = rename_to

    for res_type, old_name in declared:
        new_name = renames.get(old_name, f"{rename_to}_{old_name}" if old_name != rename_from else rename_to)
        # Rename in resource/data declarations
        result = re.sub(
            rf'((?:resource|data)\s+"{re.escape(res_type)}"\s+)"{re.escape(old_name)}"',
            rf'\1"{new_name}"',
            result,
        )
        # Rename in references (e.g., aws_s3_bucket.this.arn -> aws_s3_bucket.bucket_uploads.arn)
        result = re.sub(
            rf'\b{re.escape(res_type)}\.{re.escape(old_name)}\b',
            f'{res_type}.{new_name}',
            result,
        )

    return result


def strip_comments_header(hcl_text):
    """Remove leading comment blocks (################################ style)."""
    lines = hcl_text.split("\n")
    result = []
    skipping_header = True
    for line in lines:
        if skipping_header:
            if line.strip().startswith("#") or line.strip() == "":
                continue
            skipping_header = False
        result.append(line)
    return "\n".join(result)


def remove_for_each_resource(hcl_text, resource_pattern):
    """Remove a resource block that uses for_each (will be expanded separately)."""
    # Match the resource block and remove it
    pattern = rf'resource\s+"{resource_pattern.split(".")[0]}"\s+"{resource_pattern.split(".")[1]}"\s*\{{[^}}]*for_each[^}}]*\}}'
    # Use a more robust block matcher
    lines = hcl_text.split("\n")
    result = []
    skip_depth = 0
    skipping = False

    resource_type = resource_pattern.split(".")[0]
    resource_name = resource_pattern.split(".")[1]
    target = f'resource "{resource_type}" "{resource_name}"'

    for line in lines:
        if not skipping and target in line:
            skipping = True
            skip_depth = 0
        if skipping:
            skip_depth += line.count("{") - line.count("}")
            if skip_depth <= 0 and "{" in line or (skip_depth <= 0 and "}" in line):
                if skip_depth <= 0:
                    skipping = False
                    continue
            continue
        result.append(line)

    return "\n".join(result)


def resolve_count_blocks(hcl_text, variable, include):
    """Remove count lines and optionally remove entire resource blocks."""
    if include:
        # Keep the resource but remove the count line
        return re.sub(r'\s*count\s*=\s*[^\n]+\n', '\n', hcl_text)
    else:
        # Remove entire resource blocks that have this count
        # This is complex — for now we just remove count and let TF handle it
        # Actually, if not included, we should remove the whole resource
        return hcl_text  # TODO: implement full block removal


def resolve_dynamic_block(hcl_text, block_name, include):
    """Resolve a dynamic block to either its content or nothing."""
    if include:
        # Replace dynamic "name" { for_each = ... content { ... } }
        # with just the content block
        pattern = rf'(\s*)dynamic\s+"{re.escape(block_name)}"\s*\{{[^}}]*content\s*\{{(.*?)\}}\s*\}}'
        match = re.search(pattern, hcl_text, re.DOTALL)
        if match:
            indent = match.group(1)
            content = match.group(2)
            # Replace dynamic.value references with the actual variable ref
            replacement = f'{indent}{block_name} {{{content}}}'
            hcl_text = hcl_text[:match.start()] + replacement + hcl_text[match.end():]
    else:
        # Remove the entire dynamic block
        pattern = rf'\s*dynamic\s+"{re.escape(block_name)}"\s*\{{.*?\}}\s*\}}'
        hcl_text = re.sub(pattern, '', hcl_text, flags=re.DOTALL)

    return hcl_text


# ---------------------------------------------------------------------------
# Collect expressions (cross-wiring between modules)
# ---------------------------------------------------------------------------

def collect_access_policies(instances):
    """Build the additional_policy_jsons map from all collection instances."""
    policies = {}
    for module_id, inst_name, entry in instances:
        exports = entry.get("exports", {})
        if exports.get("access_policy_json"):
            output_map = entry.get("output_map", {})
            ref_template = output_map.get("access_policy_json", "")
            ref = ref_template.replace("{instance}", f"{entry['rename']}_{inst_name}")
            policies[f"{entry['rename']}-{inst_name}"] = ref
    return policies


def collect_env_vars(yaml_data, instances):
    """Build the environment map from YAML + auto-wired resources."""
    env_map = {}
    # Static env vars from YAML
    yaml_env = yaml_get(yaml_data, "environment") or {}
    for k, v in yaml_env.items():
        env_map[k] = (str(v), False)  # (value, is_reference)

    # Auto-wired from collection modules
    for module_id, inst_name, entry in instances:
        exports = entry.get("exports", {})
        env_export = exports.get("env_var")
        if env_export and env_export.get("target") == "environment":
            # Check if the YAML item has the env field set
            yaml_list_path = entry.get("yaml_list", "")
            items = yaml_get(yaml_data, yaml_list_path) or []
            for item in items:
                if item.get(entry.get("instance_key")) == inst_name:
                    env_field = env_export.get("yaml_field", "env")
                    env_var_name = item.get(env_field)
                    if env_var_name:
                        output = env_export["output"]
                        output_map = entry.get("output_map", {})
                        ref_template = output_map.get(output, "")
                        ref = ref_template.replace("{instance}", f"{entry['rename']}_{inst_name}")
                        env_map[env_var_name] = (ref, True)
    return env_map


def collect_secret_vars(yaml_data, instances):
    """Build the secrets map from auto-wired secret resources."""
    secret_map = {}
    for module_id, inst_name, entry in instances:
        exports = entry.get("exports", {})
        env_export = exports.get("env_var")
        if env_export and env_export.get("target") == "secrets":
            yaml_list_path = entry.get("yaml_list", "")
            items = yaml_get(yaml_data, yaml_list_path) or []
            for item in items:
                if item.get(entry.get("instance_key")) == inst_name:
                    env_field = env_export.get("yaml_field", "env")
                    env_var_name = item.get(env_field)
                    if env_var_name:
                        output = env_export["output"]
                        output_map = entry.get("output_map", {})
                        ref_template = output_map.get(output, "")
                        ref = ref_template.replace("{instance}", f"{entry['rename']}_{inst_name}")
                        secret_map[env_var_name] = (ref, True)
    return secret_map


def format_map_literal(entries):
    """Format a dict as an HCL map literal."""
    if not entries:
        return "{}"
    lines = []
    for k, (v, is_ref) in entries.items():
        if is_ref:
            lines.append(f'    "{k}" = {v}')
        else:
            lines.append(f'    "{k}" = "{v}"')
    return "{\n" + "\n".join(lines) + "\n  }"


# ---------------------------------------------------------------------------
# Reference resolver
# ---------------------------------------------------------------------------

def build_ref_resolver(registry_entries):
    """Build a function that resolves ref:module.output expressions."""
    output_maps = {}
    for entry in registry_entries:
        output_maps[entry["id"]] = entry.get("output_map", {})

    def resolver(ref):
        parts = ref.split(".", 1)
        if len(parts) != 2:
            return f"UNRESOLVED:{ref}"
        module_id, output_name = parts
        omap = output_maps.get(module_id, {})
        if output_name in omap:
            return omap[output_name]
        return f"UNRESOLVED:{ref}"

    return resolver


# ---------------------------------------------------------------------------
# File writer
# ---------------------------------------------------------------------------

def should_write(filepath):
    """Check if a file can be overwritten (has GENERATED marker or doesn't exist)."""
    if not os.path.exists(filepath):
        return True
    with open(filepath) as f:
        first_line = f.readline()
    return GENERATED_MARKER in first_line


def write_file(filepath, content):
    """Write a file with the GENERATED marker."""
    if not should_write(filepath):
        print(f"  skipped {os.path.basename(filepath)} (user-managed)")
        return
    with open(filepath, "w") as f:
        f.write(content)
    print(f"  wrote {os.path.basename(filepath)}")


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def main():
    app_yaml_path = "app.yaml"
    if not os.path.exists(app_yaml_path):
        print("No app.yaml found, skipping generation")
        return

    # Environment
    service = os.environ.get("APP_SERVICE", "")
    account_id = os.environ.get("AWS_ACCOUNT_ID", "")
    region = os.environ.get("AWS_REGION", "eu-central-1")
    tf_root = os.environ.get("TF_ROOT", "terraform")
    platform_root = os.environ.get("PLATFORM_ROOT", ".platform")

    yaml_data = load_yaml(app_yaml_path)
    app_name = yaml_data.get("name", "")
    app_team = yaml_data.get("team", "unknown")
    app_host = yaml_get(yaml_data, "routing.host") or yaml_data.get("domain", "")

    if not app_name:
        print("ERROR: app.yaml must have a 'name' field")
        sys.exit(1)

    print(f"Expanding modules for {app_name}")
    os.makedirs(tf_root, exist_ok=True)

    env_vars = {
        "AWS_ACCOUNT_ID": account_id,
        "AWS_REGION": region,
        "APP_SERVICE": service,
    }

    ref_resolver = build_ref_resolver(REGISTRY)

    # -- Pass 1: Discover all collection instances --
    collection_instances = []  # (module_id, instance_name, entry)
    for entry in REGISTRY:
        if entry["cardinality"] != "collection":
            continue
        yaml_list_path = entry.get("yaml_list", "")
        items = yaml_get(yaml_data, yaml_list_path) or []
        instance_key = entry.get("instance_key", "name")
        for item in items:
            inst_name = item.get(instance_key, "")
            if inst_name:
                collection_instances.append((entry["id"], inst_name, entry))

    # -- Pre-compute collect expressions --
    access_policies = collect_access_policies(collection_instances)
    env_var_map = collect_env_vars(yaml_data, collection_instances)
    secret_var_map = collect_secret_vars(yaml_data, collection_instances)

    # -- Pass 2: Expand each module --
    file_contents = {}  # output_file -> content

    for entry in REGISTRY:
        module_id = entry["id"]
        output_file = entry["output_file"]

        # Check condition
        condition = entry.get("condition")
        if condition:
            try:
                val, _ = resolve_expr(condition, yaml_data, env_vars)
                if val in ("false", "0", "", "null"):
                    continue
            except ValueError:
                continue

        # Read module source
        try:
            source = read_module_source(platform_root, entry["module_path"])
        except FileNotFoundError as e:
            print(f"  WARNING: {e}")
            continue

            # Read module variable defaults for unbound vars
        mod_vars = read_module_variables(platform_root, entry["module_path"])

        if entry["cardinality"] == "singleton":
            hcl = _expand_singleton(
                entry, source, yaml_data, env_vars, ref_resolver,
                access_policies, env_var_map, secret_var_map,
                mod_vars,
            )
            content = file_contents.get(output_file, "")
            file_contents[output_file] = content + hcl

        elif entry["cardinality"] == "collection":
            yaml_list_path = entry.get("yaml_list", "")
            items = yaml_get(yaml_data, yaml_list_path) or []
            if not items:
                continue

            instance_key = entry.get("instance_key", "name")
            all_hcl = ""

            for item in items:
                inst_name = item.get(instance_key, "")
                if not inst_name:
                    continue
                hcl = _expand_collection_item(
                    entry, source, yaml_data, env_vars, ref_resolver, item, inst_name,
                    mod_vars,
                )
                all_hcl += hcl

            if all_hcl:
                content = file_contents.get(output_file, "")
                file_contents[output_file] = content + all_hcl

    # -- Write boilerplate files --
    write_file(
        os.path.join(tf_root, "backend.tf"),
        BACKEND_TEMPLATE.format(
            project=PROJECT, account_id=account_id,
            service=service, region=region,
        ),
    )
    write_file(
        os.path.join(tf_root, "providers.tf"),
        PROVIDERS_TEMPLATE.format(
            region=region, project=PROJECT,
            service=service, team=app_team,
        ),
    )
    write_file(
        os.path.join(tf_root, "outputs.tf"),
        OUTPUTS_TEMPLATE.format(host=app_host),
    )

    # -- Write expanded module files --
    for filename, content in file_contents.items():
        filepath = os.path.join(tf_root, filename)
        write_file(filepath, GENERATED_MARKER + "\n" + content)

    # -- Write extra data sources --
    for entry in REGISTRY:
        extra = entry.get("extra_data_sources", [])
        if extra and entry["output_file"] in file_contents:
            filepath = os.path.join(tf_root, entry["output_file"])
            with open(filepath, "a") as f:
                for ds in extra:
                    f.write(f'\ndata "{ds["type"]}" "{ds["name"]}" {{\n')
                    f.write(f'  {ds["body"]}\n')
                    f.write("}\n")

    # -- Fingerprint --
    import hashlib
    with open(app_yaml_path, "rb") as f:
        sha = hashlib.sha256(f.read()).hexdigest()
    with open(os.path.join(tf_root, ".app-yaml-hash"), "w") as f:
        f.write(sha + "\n")

    # -- Format --
    subprocess.run(["terraform", "fmt", tf_root], capture_output=True)
    print("Expansion complete")


def _expand_singleton(entry, source, yaml_data, env_vars, ref_resolver,
                      access_policies, env_var_map, secret_var_map,
                      mod_vars=None):
    """Expand a singleton module entry into HCL."""
    rename_to = entry.get("rename", entry["id"])

    # Resolve variables
    var_values = {}
    for var_name, expr in entry.get("vars", {}).items():
        if expr == "collect:access_policy_json":
            var_values[var_name] = (format_map_literal(
                {k: (v, True) for k, v in access_policies.items()}
            ), True)
        elif expr == "collect:env_vars":
            var_values[var_name] = (format_map_literal(env_var_map), True)
        elif expr == "collect:secret_vars":
            var_values[var_name] = (format_map_literal(secret_var_map), True)
        else:
            var_values[var_name] = resolve_expr(
                expr, yaml_data, env_vars, ref_resolver=ref_resolver,
            )

    # Apply module variable defaults for any vars not in registry
    if mod_vars:
        for vname, vinfo in mod_vars.items():
            if vname not in var_values and vinfo.get("default") is not None:
                default = vinfo["default"]
                is_ref = default in ("true", "false") or default.isdigit()
                var_values[vname] = (default, is_ref)

    extra_renames = {}

    # Handle constructs
    hcl = source
    constructs = entry.get("constructs", {})

    # Remove for_each resources (expanded separately)
    fe = constructs.get("for_each_expand")
    if fe:
        resource_pattern = fe["resource"]
        # Remove the for_each resource block from source
        hcl = _remove_resource_block(hcl, resource_pattern)

    # Resolve count-based conditionals
    cr = constructs.get("count_resolve")
    if cr:
        var = cr["variable"]
        val, _ = var_values.get(var, ("true", True))
        include = val not in ("false", "0", "null")
        for res in cr.get("resources", []):
            if not include:
                hcl = _remove_resource_block(hcl, res)
            else:
                hcl = re.sub(r'\s*count\s*=\s*[^\n]+\n', '\n', hcl, count=1)

    # Expand the HCL
    hcl = expand_hcl(hcl, var_values, "this", rename_to, extra_renames)

    # Strip module-level comments
    hcl = _clean_expanded(hcl)

    # Append expanded for_each resources
    if fe:
        variable = fe["variable"]
        policies_val = var_values.get(variable, ({}, True))[0]
        if isinstance(policies_val, str) and policies_val != "{}":
            hcl += _expand_for_each_policies(
                access_policies, rename_to,
            )

    return hcl


def _expand_collection_item(entry, source, yaml_data, env_vars, ref_resolver, item, inst_name,
                            mod_vars=None):
    """Expand a collection module entry for one item."""
    rename_prefix = entry.get("rename", entry["id"])
    rename_to = f"{rename_prefix}_{inst_name}"

    # Resolve variables
    var_values = {}
    for var_name, expr in entry.get("vars", {}).items():
        var_values[var_name] = resolve_expr(
            expr, yaml_data, env_vars, item=item, ref_resolver=ref_resolver,
        )

    # Apply module variable defaults for any vars not in registry
    if mod_vars:
        for vname, vinfo in mod_vars.items():
            if vname not in var_values and vinfo.get("default") is not None:
                default = vinfo["default"]
                is_ref = default in ("true", "false") or default.isdigit()
                var_values[vname] = (default, is_ref)

    hcl = source

    # Handle constructs
    constructs = entry.get("constructs", {})

    # Resolve count-based conditionals
    cr = constructs.get("count_resolve")
    if cr:
        var = cr["variable"]
        val, _ = var_values.get(var, ("0", True))
        condition = cr.get("condition", "truthy")
        if condition == "greater_than_zero":
            include = val not in ("0", "0.0", "false", "null")
        else:
            include = val not in ("false", "0", "null")
        for res in cr.get("resources", []):
            if not include:
                hcl = _remove_resource_block(hcl, res)
            else:
                hcl = re.sub(r'\s*count\s*=\s*[^\n]+\n', '\n', hcl, count=1)

    # Resolve dynamic blocks
    dr = constructs.get("dynamic_resolve")
    if dr:
        var = dr["variable"]
        val, _ = var_values.get(var, ("null", True))
        include = val not in ("null", "", "false")
        block_name = dr["block_name"]
        hcl = resolve_dynamic_block(hcl, block_name, include)
        if include:
            # Replace attribute.value with the actual value
            hcl = re.sub(r'\battribute\.value\b', val, hcl)

    # Handle extra renames for non-"this" resources (e.g., SQS "dlq")
    extra_renames = {}
    # Find all resource names in source that aren't "this"
    for match in re.finditer(r'resource\s+"\w+"\s+"(\w+)"', source):
        name = match.group(1)
        if name != "this":
            extra_renames[name] = f"{rename_to}_{name}"

    # Also handle data sources
    for match in re.finditer(r'data\s+"\w+"\s+"(\w+)"', source):
        name = match.group(1)
        extra_renames[name] = f"{rename_to}_{name}"

    hcl = expand_hcl(hcl, var_values, "this", rename_to, extra_renames)
    hcl = _clean_expanded(hcl)

    return hcl


def _remove_resource_block(hcl, resource_pattern):
    """Remove a resource block identified by type.name pattern."""
    parts = resource_pattern.split(".")
    if len(parts) != 2:
        return hcl
    res_type, res_name = parts

    lines = hcl.split("\n")
    result = []
    skip_depth = 0
    skipping = False

    for line in lines:
        stripped = line.strip()
        if not skipping:
            if (f'resource "{res_type}" "{res_name}"' in stripped or
                    f'data "{res_type}" "{res_name}"' in stripped):
                skipping = True
                skip_depth = 0
                for ch in line:
                    if ch == '{':
                        skip_depth += 1
                    elif ch == '}':
                        skip_depth -= 1
                if skip_depth <= 0 and '{' in line:
                    skipping = skip_depth > 0
                continue
            result.append(line)
        else:
            for ch in line:
                if ch == '{':
                    skip_depth += 1
                elif ch == '}':
                    skip_depth -= 1
            if skip_depth <= 0:
                skipping = False
            # Don't append — we're removing this block

    return "\n".join(result)


def _expand_for_each_policies(access_policies, role_rename):
    """Generate individual aws_iam_role_policy resources for each policy."""
    blocks = []
    for policy_name, policy_ref in access_policies.items():
        safe_name = policy_name.replace("-", "_")
        blocks.append(f"""
resource "aws_iam_role_policy" "policy_{safe_name}" {{
  name   = "{policy_name}"
  role   = aws_iam_role.{role_rename}.id
  policy = {policy_ref}
}}
""")
    return "\n".join(blocks)


def _clean_expanded(hcl):
    """Clean up expanded HCL: remove excessive blank lines, leading/trailing whitespace."""
    # Remove lines that are just "########..." comments
    lines = hcl.split("\n")
    result = []
    for line in lines:
        if re.match(r'^\s*#{10,}\s*$', line):
            continue
        if re.match(r'^\s*#\s*\w', line):
            # Keep meaningful comments
            result.append(line)
        elif line.strip().startswith("#"):
            continue  # Skip other comment lines
        else:
            result.append(line)

    # Collapse multiple blank lines
    text = "\n".join(result)
    text = re.sub(r'\n{3,}', '\n\n', text)
    return text.strip() + "\n"


if __name__ == "__main__":
    main()
