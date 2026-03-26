#!/bin/sh
# Migrate Terraform state from expanded raw resources to module blocks.
#
# Run from the app's terraform/ directory AFTER generating module blocks
# with generate-modules.py. Creates a state backup first.
#
# Usage:
#   cd myapp/terraform
#   sh /path/to/migrate-to-modules.sh
#
# After migration, run `terraform plan` to verify zero changes.

set -e

echo "=== Creating state backup ==="
terraform state pull > terraform.tfstate.backup.json
echo "  Backed up to terraform.tfstate.backup.json"

echo ""
echo "=== Migrating state ==="

# Helper: move state if source exists
move() {
  if terraform state show "$1" >/dev/null 2>&1; then
    echo "  $1 -> $2"
    terraform state mv "$1" "$2"
  else
    echo "  skip $1 (not in state)"
  fi
}

# Platform data sources
move "data.aws_vpc.platform_main"                "module.platform.data.aws_vpc.main"
move "data.aws_subnets.platform_private"          "module.platform.data.aws_subnets.private"
move "data.aws_subnets.platform_public"           "module.platform.data.aws_subnets.public"
move "data.aws_security_group.platform_ecs_tasks" "module.platform.data.aws_security_group.ecs_tasks"
move "data.aws_lb.platform_main"                  "module.platform.data.aws_lb.main"
move "data.aws_lb_listener.platform_https"        "module.platform.data.aws_lb_listener.https"
move "data.aws_ecs_cluster.platform_main"         "module.platform.data.aws_ecs_cluster.main"
move "data.aws_iam_role.platform_ecs_execution"   "module.platform.data.aws_iam_role.ecs_execution"
move "data.aws_route53_zone.platform_main"        "module.platform.data.aws_route53_zone.main"

# ECR
move "aws_ecr_repository.ecr"            "module.ecr.aws_ecr_repository.this"
move "aws_ecr_lifecycle_policy.ecr"       "module.ecr.aws_ecr_lifecycle_policy.this"

# Routing
move "aws_lb_target_group.routing"        "module.routing.aws_lb_target_group.this"
move "aws_lb_listener_rule.routing"       "module.routing.aws_lb_listener_rule.this"
move "aws_route53_record.routing"         "module.routing.aws_route53_record.this"

# Task role
move "aws_iam_role.task"                  "module.task_role.aws_iam_role.this"
move "aws_iam_role_policy.task_logs"      "module.task_role.aws_iam_role_policy.logs"

# ECS service
move "aws_cloudwatch_log_group.main"      "module.service.aws_cloudwatch_log_group.this"
move "aws_ecs_task_definition.main"       "module.service.aws_ecs_task_definition.this"
move "aws_ecs_service.main"               "module.service.aws_ecs_service.this"

# Alarms (if present)
move "aws_cloudwatch_metric_alarm.alarm_cpu"               "module.alarms.aws_cloudwatch_metric_alarm.cpu"
move "aws_cloudwatch_metric_alarm.alarm_memory"            "module.alarms.aws_cloudwatch_metric_alarm.memory"
move "aws_cloudwatch_metric_alarm.alarm_unhealthy_targets" "module.alarms.aws_cloudwatch_metric_alarm.unhealthy_targets"
move "aws_cloudwatch_metric_alarm.alarm_target_5xx"        "module.alarms.aws_cloudwatch_metric_alarm.target_5xx"

echo ""
echo "=== Migration complete ==="
echo "Run 'terraform plan' to verify zero changes."
