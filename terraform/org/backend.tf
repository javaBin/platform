terraform {
  backend "s3" {
    bucket         = "javabin-terraform-state-553637109631"
    key            = "org/terraform.tfstate"
    region         = "eu-central-1"
    dynamodb_table = "javabin-terraform-infra-lock"
    encrypt        = true
  }
}
