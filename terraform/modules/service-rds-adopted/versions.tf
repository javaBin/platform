terraform {
  required_providers {
    aws = {
      source = "hashicorp/aws"
      # dedicated_log_volume (in ignore_changes) needs 5.40 or later.
      version = ">= 5.40, < 6.0"
    }
    random = {
      source  = "hashicorp/random"
      version = ">= 3.0"
    }
  }
}
