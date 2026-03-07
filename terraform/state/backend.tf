# Migrated from local backend to S3 after bootstrap + import.
# See docs/bootstrap-runbook.md for the full procedure.
terraform {
  backend "s3" {
    bucket         = "javabin-terraform-state-553637109631"
    key            = "state/terraform.tfstate"
    region         = "eu-central-1"
    dynamodb_table = "javabin-terraform-infra-lock"
    encrypt        = true
  }
}
