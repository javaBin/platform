# Bootstrap Runbook

One-time procedure to create the Terraform state backend and bring it under Terraform management.

## Prerequisites

- AWS CLI v2 installed
- AWS profile `javabin` configured (Identity Center SSO or IAM credentials)
- Permissions: `s3:*`, `dynamodb:*` in eu-central-1
- Terraform >= 1.7 installed

## Step 1: Run the bootstrap script

```bash
cd /path/to/javaBin/platform
./scripts/bootstrap.sh
```

This creates four resources via AWS CLI:
- `javabin-terraform-state-553637109631` (S3 bucket, versioned, KMS encrypted, public blocked)
- `javabin-terraform-infra-lock` (DynamoDB table, PAY_PER_REQUEST)
- `javabin-terraform-app-locks` (DynamoDB table, PAY_PER_REQUEST)
- `javabin-ci-plan-artifacts-553637109631` (S3 bucket, public blocked, 24h lifecycle)

## Step 2: Initialize Terraform with local backend

```bash
cd terraform/state
terraform init
```

The `backend.tf` starts as `backend "local" {}`. This is intentional -- we need local state to import the bootstrapped resources.

## Step 3: Import each resource

Import the resources created by the bootstrap script into Terraform state:

```bash
# State bucket
terraform import aws_s3_bucket.state javabin-terraform-state-553637109631
terraform import aws_s3_bucket_versioning.state javabin-terraform-state-553637109631
terraform import aws_s3_bucket_server_side_encryption_configuration.state javabin-terraform-state-553637109631
terraform import aws_s3_bucket_public_access_block.state javabin-terraform-state-553637109631

# Infra lock table
terraform import aws_dynamodb_table.infra_lock javabin-terraform-infra-lock

# App lock table
terraform import aws_dynamodb_table.app_locks javabin-terraform-app-locks

# Plan artifacts bucket
terraform import aws_s3_bucket.plan_artifacts javabin-ci-plan-artifacts-553637109631
terraform import aws_s3_bucket_public_access_block.plan_artifacts javabin-ci-plan-artifacts-553637109631
terraform import aws_s3_bucket_lifecycle_configuration.plan_artifacts javabin-ci-plan-artifacts-553637109631
```

## Step 4: Verify no changes

```bash
terraform plan
```

The plan should show no changes (or very minor drift to reconcile). Fix any differences before proceeding.

## Step 5: Switch backend to S3

Edit `terraform/state/backend.tf`. Replace the entire file with:

```hcl
terraform {
  backend "s3" {
    bucket         = "javabin-terraform-state-553637109631"
    key            = "state/terraform.tfstate"
    region         = "eu-central-1"
    dynamodb_table = "javabin-terraform-infra-lock"
    encrypt        = true
  }
}
```

## Step 6: Migrate state to S3

```bash
terraform init -migrate-state
```

Terraform will ask to copy the local state to S3. Confirm with `yes`.

## Step 7: Clean up local state

```bash
rm -f terraform.tfstate terraform.tfstate.backup
```

## Step 8: Verify

```bash
terraform plan
```

Should show no changes, and state is now stored in S3.

## Step 9: Commit

```bash
cd ../..
git add terraform/state/ scripts/bootstrap.sh docs/bootstrap-runbook.md
git commit -m "Bootstrap state backend: S3 + DynamoDB + plan artifacts"
```

## State layout in S3

After bootstrap, the state bucket holds:

```
javabin-terraform-state-553637109631/
  state/terraform.tfstate              # this module (state management)
  infra/terraform.tfstate              # platform module (Phase 2+)
  org/terraform.tfstate                # org module (SCPs, Organizations)
  apps/{repo-name}/terraform.tfstate   # one per registered app repo
```

## Troubleshooting

**"BucketAlreadyOwnedByYou"**: The bootstrap script is idempotent for bucket creation. If the bucket already exists in your account, this error is harmless. Proceed with the remaining steps.

**DynamoDB table already exists**: If a table already exists, the `create-table` call will fail. Skip that step and proceed with the import.

**Import drift**: If `terraform plan` shows changes after import, review them carefully. Minor attribute differences (e.g., tags) are expected and safe to apply.
