#!/bin/sh
# Generate Terraform files from app.yaml
#
# Generates backend.tf, providers.tf, main.tf into $TF_ROOT.
# Only overwrites files that have the GENERATED marker (or don't exist yet).
# Files without the marker are user-managed and left untouched.
#
# Single-env (no environments: key):
#   Generates files in $TF_ROOT/ (current behavior)
#
# Multi-env (environments: key present):
#   Generates files in $TF_ROOT/{env}/ for each environment
#   Prod environment creates the ECR repo; others reference it via data source
#
# Required env vars: APP_SERVICE, AWS_ACCOUNT_ID, AWS_REGION, TF_ROOT

set -e

APP_YAML="app.yaml"
MARKER="# GENERATED FROM app.yaml — do not edit, changes will be overwritten"

if [ ! -f "$APP_YAML" ]; then
  echo "No app.yaml found, skipping generation"
  exit 0
fi

echo "Generating Terraform from $APP_YAML"
mkdir -p "$TF_ROOT"

# Only overwrite a file if it doesn't exist or has the GENERATED marker
should_write() {
  local file="$1"
  [ ! -f "$file" ] || head -1 "$file" | grep -qF "$MARKER" 2>/dev/null
}

# Extract a top-level scalar value from app.yaml (simple grep, no yq dependency)
yaml_val() {
  grep -m1 "^${1}:" "$APP_YAML" | sed "s/^${1}:[[:space:]]*//" | sed 's/[[:space:]]*#.*//' | tr -d '"' | tr -d "'"
}

# List environment names from the environments: block
list_environments() {
  awk '
    /^environments:/ { in_envs=1; next }
    in_envs && /^[^ ]/ { exit }
    in_envs && /^  [a-z][a-z0-9]*:/ { sub(/:.*/, ""); sub(/^  /, ""); print }
  ' "$APP_YAML"
}

APP_NAME=$(yaml_val name)
APP_TEAM=$(yaml_val team)
APP_AUTH=$(yaml_val auth)

if [ -z "$APP_NAME" ]; then
  echo "ERROR: app.yaml must have a 'name' field"
  exit 1
fi

# Detect multi-environment mode
ENVIRONMENTS=$(list_environments)

# Generate Terraform files for a single environment directory
# Args: $1=output_dir $2=state_key_suffix $3=env_name $4=env_tag $5=create_ecr $6=config_path
generate_env() {
  local out_dir="$1"
  local state_suffix="$2"
  local env_name="$3"
  local env_tag="$4"
  local create_ecr="$5"
  local config_path="$6"

  mkdir -p "$out_dir"

  # --- backend.tf ---
  if should_write "$out_dir/backend.tf"; then
    cat > "$out_dir/backend.tf" << EOF
${MARKER}
terraform {
  backend "s3" {
    bucket         = "javabin-terraform-state-${AWS_ACCOUNT_ID}"
    key            = "apps/${APP_SERVICE}${state_suffix}/terraform.tfstate"
    region         = "${AWS_REGION}"
    dynamodb_table = "javabin-terraform-app-locks"
    encrypt        = true
  }
}
EOF
    echo "  wrote ${out_dir}/backend.tf"
  fi

  # --- providers.tf ---
  if should_write "$out_dir/providers.tf"; then
    cat > "$out_dir/providers.tf" << EOF
${MARKER}
terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  region = "${AWS_REGION}"

  default_tags {
    tags = {
      project     = "javabin"
      managed-by  = "terraform"
      service     = "${APP_SERVICE}"
      team        = "${APP_TEAM:-unknown}"
      environment = "${env_tag}"
    }
  }
}
EOF
    echo "  wrote ${out_dir}/providers.tf"
  fi

  # --- main.tf ---
  if should_write "$out_dir/main.tf"; then
    {
      echo "${MARKER}"
      echo 'module "app" {'
      echo '  source = "git::https://github.com/javaBin/platform.git//terraform/modules/app-stack?ref=main"'
      echo ''
      echo "  config_file      = \"${config_path}\""
      echo "  aws_account_id   = \"${AWS_ACCOUNT_ID}\""
      if [ -n "$env_name" ]; then
        echo "  environment_name = \"${env_name}\""
      fi
      if [ "$create_ecr" = "false" ]; then
        echo "  create_ecr       = false"
      fi
      echo '}'
    } > "$out_dir/main.tf"
    echo "  wrote ${out_dir}/main.tf"
  fi
}

if [ -n "$ENVIRONMENTS" ]; then
  # Multi-environment mode
  echo "  detected environments: $ENVIRONMENTS"

  # Determine which env creates ECR (prod if it exists, otherwise first)
  ECR_ENV=""
  for env in $ENVIRONMENTS; do
    if [ "$env" = "prod" ]; then
      ECR_ENV="prod"
      break
    fi
  done
  if [ -z "$ECR_ENV" ]; then
    ECR_ENV=$(echo "$ENVIRONMENTS" | head -1)
  fi

  for env in $ENVIRONMENTS; do
    create_ecr="false"
    if [ "$env" = "$ECR_ENV" ]; then
      create_ecr="true"
    fi

    env_tag="$env"
    if [ "$env" = "prod" ]; then
      env_tag="production"
    fi

    generate_env \
      "$TF_ROOT/$env" \
      "-${env}" \
      "$env" \
      "$env_tag" \
      "$create_ecr" \
      '${path.root}/../../app.yaml'
  done
else
  # Single-environment mode (backwards compatible)
  generate_env \
    "$TF_ROOT" \
    "" \
    "" \
    "production" \
    "true" \
    '${path.root}/../app.yaml'
fi

# Write a hash of app.yaml so terraform/ always has a git diff when YAML changes.
# Without this, YAML-only changes (e.g. adding a bucket) don't change the committed
# TF files, so the terraform track's `changes: terraform/**/*` rule never triggers.
if command -v sha256sum >/dev/null 2>&1; then
  sha256sum "$APP_YAML" | cut -d' ' -f1 > "$TF_ROOT/.app-yaml-hash"
else
  shasum -a 256 "$APP_YAML" | cut -d' ' -f1 > "$TF_ROOT/.app-yaml-hash"
fi

# Format generated files
if [ -n "$ENVIRONMENTS" ]; then
  for env in $ENVIRONMENTS; do
    terraform fmt "$TF_ROOT/$env" > /dev/null || true
  done
else
  terraform fmt "$TF_ROOT" > /dev/null || true
fi
