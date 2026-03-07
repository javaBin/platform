#!/bin/bash
################################################################################
# Bootstrap script — creates Terraform state resources via AWS CLI
#
# This is the ONLY manual AWS CLI step in the entire platform. Run once, then
# manage these resources via terraform/state/ module with import + migrate.
#
# Prerequisites:
#   - AWS CLI v2 installed
#   - AWS profile "javabin" configured (Identity Center or IAM credentials)
#   - Sufficient permissions: s3:*, dynamodb:*
#
# Usage:
#   ./scripts/bootstrap.sh
################################################################################
set -euo pipefail

PROFILE="javabin"
REGION="eu-central-1"
ACCOUNT_ID="553637109631"
PROJECT="javabin"

STATE_BUCKET="${PROJECT}-terraform-state-${ACCOUNT_ID}"
INFRA_LOCK_TABLE="${PROJECT}-terraform-infra-lock"
APP_LOCK_TABLE="${PROJECT}-terraform-app-locks"
PLAN_ARTIFACTS_BUCKET="${PROJECT}-ci-plan-artifacts-${ACCOUNT_ID}"

echo "=== Javabin Platform Bootstrap ==="
echo ""
echo "Region:              ${REGION}"
echo "State bucket:        ${STATE_BUCKET}"
echo "Infra lock table:    ${INFRA_LOCK_TABLE}"
echo "App lock table:      ${APP_LOCK_TABLE}"
echo "Plan artifacts:      ${PLAN_ARTIFACTS_BUCKET}"
echo ""

################################################################################
# 1. State bucket
################################################################################
echo "[1/6] Creating state bucket: ${STATE_BUCKET}"
aws s3api create-bucket \
  --profile "${PROFILE}" \
  --bucket "${STATE_BUCKET}" \
  --region "${REGION}" \
  --create-bucket-configuration LocationConstraint="${REGION}"

echo "[2/6] Enabling versioning on state bucket"
aws s3api put-bucket-versioning \
  --profile "${PROFILE}" \
  --bucket "${STATE_BUCKET}" \
  --versioning-configuration Status=Enabled

echo "       Enabling KMS encryption on state bucket"
aws s3api put-bucket-encryption \
  --profile "${PROFILE}" \
  --bucket "${STATE_BUCKET}" \
  --server-side-encryption-configuration \
  '{"Rules":[{"ApplyServerSideEncryptionByDefault":{"SSEAlgorithm":"aws:kms"}}]}'

echo "       Blocking public access on state bucket"
aws s3api put-public-access-block \
  --profile "${PROFILE}" \
  --bucket "${STATE_BUCKET}" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

################################################################################
# 2. DynamoDB lock tables
################################################################################
echo "[3/6] Creating infra lock table: ${INFRA_LOCK_TABLE}"
aws dynamodb create-table \
  --profile "${PROFILE}" \
  --table-name "${INFRA_LOCK_TABLE}" \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "${REGION}" \
  --tags Key=project,Value="${PROJECT}" Key=managed-by,Value=terraform Key=team,Value=javabin

echo "[4/6] Creating app lock table: ${APP_LOCK_TABLE}"
aws dynamodb create-table \
  --profile "${PROFILE}" \
  --table-name "${APP_LOCK_TABLE}" \
  --attribute-definitions AttributeName=LockID,AttributeType=S \
  --key-schema AttributeName=LockID,KeyType=HASH \
  --billing-mode PAY_PER_REQUEST \
  --region "${REGION}" \
  --tags Key=project,Value="${PROJECT}" Key=managed-by,Value=terraform Key=team,Value=javabin

################################################################################
# 3. Plan artifacts bucket
################################################################################
echo "[5/6] Creating plan artifacts bucket: ${PLAN_ARTIFACTS_BUCKET}"
aws s3api create-bucket \
  --profile "${PROFILE}" \
  --bucket "${PLAN_ARTIFACTS_BUCKET}" \
  --region "${REGION}" \
  --create-bucket-configuration LocationConstraint="${REGION}"

echo "       Blocking public access on plan artifacts bucket"
aws s3api put-public-access-block \
  --profile "${PROFILE}" \
  --bucket "${PLAN_ARTIFACTS_BUCKET}" \
  --public-access-block-configuration \
  BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true

echo "[6/6] Adding 24h lifecycle expiry to plan artifacts bucket"
aws s3api put-bucket-lifecycle-configuration \
  --profile "${PROFILE}" \
  --bucket "${PLAN_ARTIFACTS_BUCKET}" \
  --lifecycle-configuration '{
    "Rules": [{
      "ID": "expire-plan-artifacts-24h",
      "Status": "Enabled",
      "Filter": {},
      "Expiration": { "Days": 1 }
    }]
  }'

echo ""
echo "=== Bootstrap complete ==="
echo ""
echo "Next steps:"
echo "  1. cd terraform/state"
echo "  2. terraform init"
echo "  3. Import each resource (see docs/bootstrap-runbook.md)"
echo "  4. Switch backend.tf from local to S3"
echo "  5. terraform init -migrate-state"
echo "  6. rm -f terraform.tfstate terraform.tfstate.backup"
echo "  7. Commit"
