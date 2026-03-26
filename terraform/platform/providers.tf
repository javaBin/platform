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
    service     = "platform"
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

# us-east-1 alias — required for CloudFront and public ECR
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = local.required_tags
  }
}
