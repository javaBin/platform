"""Module registry — maps app.yaml sections to platform Terraform modules.

This is the ONLY place modules are defined. Adding a new resource type to the
platform means adding a registry entry here. The expander (expand-modules.py)
reads this registry and the module source files to generate expanded Terraform.
"""

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

PROJECT = "javabin"
DOMAIN = "javazone.no"
MODULE_ROOT = "terraform/modules"

# ---------------------------------------------------------------------------
# Registry entries
#
# Each entry describes one module and how it maps to app.yaml:
#
#   id             Unique key, used for resource renaming and cross-references
#   module_path    Path to module source (relative to platform repo root)
#   output_file    Generated .tf file name in the app repo
#   cardinality    "singleton" (one per app) or "collection" (N from YAML list)
#   condition      If set, a DSL expression that must be truthy to emit this module
#   vars           Dict of module_variable -> value expression (DSL)
#   rename         Resource name to replace "this" with (singletons)
#   output_map     Maps module output names -> expanded resource attribute paths
#   constructs     Hints for complex HCL constructs (for_each, dynamic, count)
#   yaml_list      (collections) Dot-path to the YAML array
#   instance_key   (collections) YAML field used as instance name
#   exports        (collections) What this module contributes to cross-wiring
# ---------------------------------------------------------------------------

REGISTRY = [
    # ------------------------------------------------------------------
    # platform-data: shared infrastructure data sources (always emitted)
    # ------------------------------------------------------------------
    {
        "id": "platform",
        "module_path": f"{MODULE_ROOT}/platform-data",
        "output_file": "platform.tf",
        "cardinality": "singleton",
        "vars": {
            "project": f"const:{PROJECT}",
            "domain": f"const:{DOMAIN}",
        },
        "rename": "platform",
        "output_map": {
            "vpc_id": "data.aws_vpc.platform_main.id",
            "private_subnet_ids": "data.aws_subnets.platform_private.ids",
            "public_subnet_ids": "data.aws_subnets.platform_public.ids",
            "ecs_tasks_security_group_id": "data.aws_security_group.platform_ecs_tasks.id",
            "alb_arn": "data.aws_lb.platform_main.arn",
            "alb_dns_name": "data.aws_lb.platform_main.dns_name",
            "alb_zone_id": "data.aws_lb.platform_main.zone_id",
            "alb_arn_suffix": "data.aws_lb.platform_main.arn_suffix",
            "https_listener_arn": "data.aws_lb_listener.platform_https.arn",
            "ecs_cluster_id": "data.aws_ecs_cluster.platform_main.id",
            "ecs_cluster_name": "data.aws_ecs_cluster.platform_main.cluster_name",
            "execution_role_arn": "data.aws_iam_role.platform_ecs_execution.arn",
            "route53_zone_id": "data.aws_route53_zone.platform_main.zone_id",
            # Boundary ARN constructed from account ID — no data source needed.
            # Avoids iam:GetPolicy permission requirement on the boundary policy.
            "developer_boundary_arn": "NOT_USED",
        },
    },

    # ------------------------------------------------------------------
    # ecr-repo: container registry
    # ------------------------------------------------------------------
    {
        "id": "ecr",
        "module_path": f"{MODULE_ROOT}/ecr-repo",
        "output_file": "ecr.tf",
        "cardinality": "singleton",
        "vars": {
            "name": "yaml:name",
            "team": "yaml:team",
        },
        "rename": "ecr",
        "output_map": {
            "repository_url": "aws_ecr_repository.ecr.repository_url",
            "repository_arn": "aws_ecr_repository.ecr.arn",
            "repository_name": "aws_ecr_repository.ecr.name",
        },
    },

    # ------------------------------------------------------------------
    # service-routing: ALB target group + listener rule + DNS
    # ------------------------------------------------------------------
    {
        "id": "routing",
        "module_path": f"{MODULE_ROOT}/service-routing",
        "output_file": "routing.tf",
        "cardinality": "singleton",
        "vars": {
            "name": "yaml:name",
            "team": "yaml:team",
            "vpc_id": "ref:platform.vpc_id",
            "port": "yaml:compute.port|default:8000",
            "health_check_path": "yaml:compute.health_check|default:/health",
            "health_check_matcher": "yaml:compute.health_check_matcher|default:200",
            "https_listener_arn": "ref:platform.https_listener_arn",
            "listener_rule_priority": "yaml:routing.priority",
            "host_header": "yaml:routing.host",
            "route53_zone_id": "ref:platform.route53_zone_id",
            "alb_dns_name": "ref:platform.alb_dns_name",
            "alb_zone_id": "ref:platform.alb_zone_id",
            "deregistration_delay": "yaml:routing.deregistration_delay|default:30",
        },
        "rename": "routing",
        "output_map": {
            "target_group_arn": "aws_lb_target_group.routing.arn",
            "target_group_arn_suffix": "aws_lb_target_group.routing.arn_suffix",
            "dns_name": "aws_route53_record.routing.fqdn",
        },
    },

    # ------------------------------------------------------------------
    # service-role: task IAM role + policies (ECS, Lambda, or EC2)
    # ------------------------------------------------------------------
    {
        "id": "task_role",
        "module_path": f"{MODULE_ROOT}/service-role",
        "output_file": "iam.tf",
        "cardinality": "singleton",
        "vars": {
            "name": "yaml:name",
            "team": "yaml:team",
            "region": "env:AWS_REGION",
            "aws_account_id": "env:AWS_ACCOUNT_ID",
            "permissions_boundary_arn": f"expr:arn:aws:iam::${{env:AWS_ACCOUNT_ID}}:policy/{PROJECT}-developer-boundary",
            "trusted_services": "list:yaml:compute.trusted_service|default:ecs-tasks.amazonaws.com",
            "additional_policy_jsons": "collect:access_policy_json",
        },
        "rename": "task",
        "output_map": {
            "role_arn": "aws_iam_role.task.arn",
            "role_id": "aws_iam_role.task.id",
            "role_name": "aws_iam_role.task.name",
        },
        "constructs": {
            # for_each on additional_policy_jsons: expand into N named resources
            "for_each_expand": {
                "resource": "aws_iam_role_policy.additional",
                "variable": "additional_policy_jsons",
            },
        },
    },

    # ------------------------------------------------------------------
    # ecs-service: log group + task definition + ECS service
    # ------------------------------------------------------------------
    {
        "id": "service",
        "module_path": f"{MODULE_ROOT}/ecs-service",
        "output_file": "service.tf",
        "cardinality": "singleton",
        "vars": {
            "name": "yaml:name",
            "team": "yaml:team",
            "cluster_id": "ref:platform.ecs_cluster_id",
            "image": "expr:${ref:ecr.repository_url}:latest",
            "cpu": "yaml:compute.cpu|default:512",
            "memory": "yaml:compute.memory|default:1024",
            "port": "yaml:compute.port|default:8000",
            "desired_count": "yaml:compute.desired_count|default:1",
            "execution_role_arn": "ref:platform.execution_role_arn",
            "task_role_arn": "ref:task_role.role_arn",
            "subnet_ids": "ref:platform.private_subnet_ids",
            "security_group_ids": "list:ref:platform.ecs_tasks_security_group_id",
            "target_group_arn": "ref:routing.target_group_arn",
            "region": "env:AWS_REGION",
            "container_user": "yaml:compute.user|default:1000",
            "environment": "collect:env_vars",
            "secrets": "collect:secret_vars",
        },
        "rename": "main",
        "output_map": {
            "service_name": "aws_ecs_service.main.name",
            "task_definition_arn": "aws_ecs_task_definition.main.arn",
            "log_group_name": "aws_cloudwatch_log_group.main.name",
        },
    },

    # ------------------------------------------------------------------
    # service-alarm: CloudWatch alarms (conditional)
    # ------------------------------------------------------------------
    {
        "id": "alarms",
        "module_path": f"{MODULE_ROOT}/service-alarm",
        "output_file": "alarms.tf",
        "cardinality": "singleton",
        "condition": "yaml:alarms.enabled|default:true",
        "vars": {
            "name": "yaml:name",
            "team": "yaml:team",
            "service": "yaml:name",
            "cluster_name": "ref:platform.ecs_cluster_name",
            "sns_topic_arns": "list:data.aws_sns_topic.alerts.arn",
            "cpu_threshold": "yaml:alarms.cpu_threshold|default:80",
            "memory_threshold": "yaml:alarms.memory_threshold|default:80",
            "error_5xx_threshold": "yaml:alarms.error_5xx_threshold|default:10",
            "enable_alb_alarms": "const:true",
            "target_group_arn_suffix": "ref:routing.target_group_arn_suffix",
            "alb_arn_suffix": "ref:platform.alb_arn_suffix",
        },
        "rename": "alarm",
        "extra_data_sources": [
            {
                "type": "aws_sns_topic",
                "name": "alerts",
                "body": f'name = "{PROJECT}-alerts"',
            },
        ],
        "constructs": {
            # count-based conditional resources: resolve at gen time
            "count_resolve": {
                "variable": "enable_alb_alarms",
                "resources": ["aws_cloudwatch_metric_alarm.unhealthy_targets",
                              "aws_cloudwatch_metric_alarm.target_5xx"],
            },
        },
    },

    # ------------------------------------------------------------------
    # Collection modules: one set of resources per YAML list item
    # ------------------------------------------------------------------
    {
        "id": "bucket",
        "module_path": f"{MODULE_ROOT}/service-bucket",
        "output_file": "buckets.tf",
        "cardinality": "collection",
        "yaml_list": "resources.buckets",
        "instance_key": "name",
        "vars": {
            "name": "item:name",
            "team": "yaml:team",
            "aws_account_id": "env:AWS_ACCOUNT_ID",
            "service": "yaml:name",
            "versioning": "item:versioning|default:true",
            "expire_days": "item:expire_days|default:0",
        },
        "rename": "bucket",
        "output_map": {
            "bucket_name": "aws_s3_bucket.{instance}.id",
            "bucket_arn": "aws_s3_bucket.{instance}.arn",
            "access_policy_json": "data.aws_iam_policy_document.{instance}_access.json",
        },
        "exports": {
            "access_policy_json": True,
            "env_var": {"output": "bucket_name", "yaml_field": "env", "target": "environment"},
        },
        "constructs": {
            "count_resolve": {
                "variable": "expire_days",
                "condition": "greater_than_zero",
                "resources": ["aws_s3_bucket_lifecycle_configuration.this"],
            },
        },
    },
    {
        "id": "database",
        "module_path": f"{MODULE_ROOT}/service-database",
        "output_file": "databases.tf",
        "cardinality": "collection",
        "yaml_list": "resources.databases",
        "instance_key": "name",
        "engine_filter": "dynamodb",
        "vars": {
            "name": "item:name",
            "team": "yaml:team",
            "service": "yaml:name",
            "hash_key": "item:hash_key|default:id",
            "hash_key_type": "item:hash_key_type|default:S",
            "range_key": "item:range_key|default:null",
            "range_key_type": "item:range_key_type|default:S",
            "ttl_attribute": "item:ttl_attribute|default:",
        },
        "rename": "db",
        "output_map": {
            "table_name": "aws_dynamodb_table.{instance}.name",
            "table_arn": "aws_dynamodb_table.{instance}.arn",
            "access_policy_json": "data.aws_iam_policy_document.{instance}_access.json",
        },
        "exports": {
            "access_policy_json": True,
            "env_var": {"output": "table_name", "yaml_field": "env", "target": "environment"},
        },
        "constructs": {
            "dynamic_resolve": {
                "variable": "range_key",
                "block_name": "attribute",
                "condition": "not_null",
            },
        },
    },
    {
        "id": "rds",
        "module_path": f"{MODULE_ROOT}/service-rds",
        "output_file": "databases.tf",
        "cardinality": "collection",
        "yaml_list": "resources.databases",
        "instance_key": "name",
        "engine_filter": "postgres",
        "vars": {
            "name": "item:name",
            "team": "yaml:team",
            "project": f"const:{PROJECT}",
            "engine_version": "item:engine_version|default:16",
            "instance_class": "item:instance_class|default:db.t3.micro",
            "allocated_storage": "item:allocated_storage|default:20",
            "subnet_ids": "ref:platform.private_subnet_ids",
            "vpc_id": "ref:platform.vpc_id",
            "allowed_security_group_ids": "list:ref:platform.ecs_tasks_security_group_id",
            "backup_retention_period": "item:backup_retention_period|default:7",
            "multi_az": "item:multi_az|default:false",
            "deletion_protection": "item:deletion_protection|default:true",
        },
        "rename": "rds",
        "output_map": {
            "endpoint": "aws_db_instance.{instance}.endpoint",
            "port": "aws_db_instance.{instance}.port",
            "db_name": "aws_db_instance.{instance}.db_name",
            "access_policy_json": "data.aws_iam_policy_document.{instance}_access.json",
            "security_group_id": "aws_security_group.{instance}.id",
        },
        "exports": {
            "access_policy_json": True,
            "env_var": {"output": "endpoint", "yaml_field": "env", "target": "environment"},
        },
    },
    {
        "id": "secret",
        "module_path": f"{MODULE_ROOT}/service-secret",
        "output_file": "secrets.tf",
        "cardinality": "collection",
        "yaml_list": "resources.secrets",
        "instance_key": "name",
        "vars": {
            "name": "item:name",
            "team": "yaml:team",
            "project": f"const:{PROJECT}",
            "service": "yaml:name",
            "description": "item:description|default:",
        },
        "rename": "secret",
        "output_map": {
            "parameter_arn": "aws_ssm_parameter.{instance}.arn",
            "access_policy_json": "data.aws_iam_policy_document.{instance}_access.json",
        },
        "exports": {
            "access_policy_json": True,
            "env_var": {"output": "parameter_arn", "yaml_field": "env", "target": "secrets"},
        },
    },
    {
        "id": "queue",
        "module_path": f"{MODULE_ROOT}/service-queue",
        "output_file": "queues.tf",
        "cardinality": "collection",
        "yaml_list": "resources.queues",
        "instance_key": "name",
        "vars": {
            "name": "item:name",
            "team": "yaml:team",
            "service": "yaml:name",
            "visibility_timeout_seconds": "item:visibility_timeout|default:30",
            "retention_seconds": "item:retention_seconds|default:345600",
            "max_receive_count": "item:max_receive_count|default:3",
        },
        "rename": "queue",
        "output_map": {
            "queue_url": "aws_sqs_queue.{instance}.url",
            "queue_arn": "aws_sqs_queue.{instance}.arn",
            "dlq_url": "aws_sqs_queue.{instance}_dlq.url",
            "access_policy_json": "data.aws_iam_policy_document.{instance}_access.json",
        },
        "exports": {
            "access_policy_json": True,
            "env_var": {"output": "queue_url", "yaml_field": "env", "target": "environment"},
        },
    },
]


# ---------------------------------------------------------------------------
# Boilerplate templates (not module-derived)
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
  value = aws_ecr_repository.ecr.repository_url
}}

output "service_name" {{
  value = aws_ecs_service.main.name
}}

output "task_role_arn" {{
  value = aws_iam_role.task.arn
}}

output "log_group" {{
  value = aws_cloudwatch_log_group.main.name
}}
"""
