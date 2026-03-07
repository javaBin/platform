terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

locals {
  required_tags = {
    project     = var.project
    team        = "javabin"
    managed-by  = "terraform"
    environment = var.environment
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.required_tags
  }
}
