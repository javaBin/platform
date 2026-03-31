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
    team        = "platform"
    service     = "state"
    repo        = "javaBin/platform"
    environment = var.environment
    managed-by  = "terraform"
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = local.required_tags
  }
}
