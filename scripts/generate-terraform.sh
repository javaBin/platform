#!/bin/sh
# Generate Terraform files from app.yaml
#
# Generates backend.tf, providers.tf, main.tf into $TF_ROOT.
# Only overwrites files that have the GENERATED marker (or don't exist yet).
# Files without the marker are user-managed and left untouched.
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

APP_NAME=$(yaml_val name)
APP_TEAM=$(yaml_val team)
APP_AUTH=$(yaml_val auth)

if [ -z "$APP_NAME" ]; then
  echo "ERROR: app.yaml must have a 'name' field"
  exit 1
fi

# --- backend.tf ---
if should_write "$TF_ROOT/backend.tf"; then
  cat > "$TF_ROOT/backend.tf" << EOF
${MARKER}
terraform {
  backend "s3" {
    bucket         = "javabin-terraform-state-${AWS_ACCOUNT_ID}"
    key            = "apps/${APP_SERVICE}/terraform.tfstate"
    region         = "${AWS_REGION}"
    dynamodb_table = "javabin-terraform-app-locks"
    encrypt        = true
  }
}
EOF
  echo "  wrote backend.tf"
fi

# --- providers.tf ---
if should_write "$TF_ROOT/providers.tf"; then
  cat > "$TF_ROOT/providers.tf" << EOF
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
      environment = "production"
    }
  }
}
EOF
  echo "  wrote providers.tf"
fi

# --- main.tf ---
if should_write "$TF_ROOT/main.tf"; then
  {
    echo "${MARKER}"
    echo 'module "app" {'
    echo '  source = "git::https://github.com/javaBin/platform.git//terraform/modules/app-stack?ref=main"'
    echo ''
    echo '  config_file    = "${path.root}/../app.yaml"'
    echo "  aws_account_id = \"${AWS_ACCOUNT_ID}\""
    echo '}'

    # Cognito app client module — deferred until Phase 1 (Identity) is implemented.
    # When Cognito user pools exist, this will generate a cognito-app-client module
    # block for apps with auth: internal/external/both in their app.yaml.
  } > "$TF_ROOT/main.tf"
  echo "  wrote main.tf"
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
terraform fmt "$TF_ROOT" > /dev/null || true
