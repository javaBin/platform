"""Module registry — constants and templates for Terraform generation.

Used by generate-modules.py to produce module {} blocks from app.yaml.
The old expand-modules.py used the REGISTRY list and DSL expressions — that
approach is deprecated. Module blocks let Terraform handle variable resolution
and cross-module references natively.
"""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT = "javabin"
DOMAIN = "javazone.no"

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
  value = "https://{host}"
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
