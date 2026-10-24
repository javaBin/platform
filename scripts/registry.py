"""Module registry — constants and templates for Terraform generation.

Used by generate-modules.py to produce module {} blocks from app.yaml.
Module blocks let Terraform handle variable resolution and cross-module
references natively.
"""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT = "javabin"
DOMAIN = "javazone.no"
# Domains apps may be hosted under. Each has a Route53 zone and a wildcard
# cert on the platform ALB, so a host must be exactly one label under one.
APP_DOMAINS = ("javazone.no", "java.no", "javabin.no")

# Existing resources a team may adopt with app.yaml (databases[].adopt and
# routing.adopt_dns). Adopting takes over a resource the platform did not
# create: the team's Terraform imports or overwrites it and then owns it.
# Neither Route53 records nor untagged legacy resources are protected by the
# team permission boundary, so each adoption is approved here, in a platform
# PR, instead of being a one-line app.yaml change. Remove an entry once the
# adoption is applied if the resource should never be adopted again.
ADOPTIONS = {
    "moresleep": {
        "rds": {"dbmoresleep"},
        "dns": {"sleepingpill.javazone.no"},
    },
}

# ---------------------------------------------------------------------------
# Module source paths (relative to platform repo root)
# ---------------------------------------------------------------------------

MODULE_SOURCES = {
    "platform-data":      "terraform/modules/platform-data",
    "ecr-repo":           "terraform/modules/ecr-repo",
    "service-routing":    "terraform/modules/service-routing",
    "service-role":       "terraform/modules/service-role",
    "ecs-service":        "terraform/modules/ecs-service",
    "service-alarm":      "terraform/modules/service-alarm",
    "service-bucket":     "terraform/modules/service-bucket",
    "service-database":   "terraform/modules/service-database",
    "service-rds":        "terraform/modules/service-rds",
    "service-rds-adopted": "terraform/modules/service-rds-adopted",
    "service-secret":     "terraform/modules/service-secret",
    "service-queue":      "terraform/modules/service-queue",
    "cognito-app-client": "terraform/modules/cognito-app-client",
}

# ---------------------------------------------------------------------------
# Boilerplate templates
# ---------------------------------------------------------------------------

BACKEND_TEMPLATE = """\
# GENERATED FROM app.yaml — do not edit, changes will be overwritten
terraform {{
  backend "s3" {{
    bucket         = "{project}-terraform-state-{account_id}"
    key            = "apps/{team}/{service}/terraform.tfstate"
    region         = "{region}"
    dynamodb_table = "{project}-terraform-app-locks"
    encrypt        = true
  }}
}}
"""

PROVIDERS_TEMPLATE = """\
# GENERATED FROM app.yaml — do not edit, changes will be overwritten
terraform {{
  required_version = ">= 1.5"

  required_providers {{
    aws = {{
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }}
  }}
}}

provider "aws" {{
  region = "{region}"

  default_tags {{
    tags = {{
      team        = "{team}"
      service     = "{service}"
      repo        = "{repo}"
      environment = "production"
      managed-by  = "terraform"
    }}
  }}
}}
"""

GITIGNORE_TEMPLATE = """\
# Terraform working files — never commit these
.terraform/
.terraform.lock.hcl
*.tfstate
*.tfstate.backup
*.tfplan
override.tf
override.tf.json
"""

OUTPUTS_TEMPLATE = """\
# GENERATED FROM app.yaml — do not edit, changes will be overwritten
output "service_url" {{
  value = "{service_url}"
}}

output "ecr_url" {{
  value = module.ecr.repository_url
}}

output "service_name" {{
  value = module.service.service_name
}}

output "task_role_arn" {{
  value = module.task_role.role_arn
}}

output "log_group" {{
  value = module.service.log_group_name
}}
"""
