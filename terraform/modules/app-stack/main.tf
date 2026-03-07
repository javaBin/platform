################################################################################
# YAML Parsing + Defaults
################################################################################

locals {
  raw = yamldecode(file(var.config_file))

  # Top-level (name validated: lowercase alphanumeric + hyphens, max 20 chars for ALB target group limits)
  name                 = regex("^[a-z][a-z0-9-]{0,19}$", local.raw.name)
  port                 = try(local.raw.compute.port, 8000)
  health_check         = try(local.raw.compute.health_check, "/health")
  health_check_matcher = try(local.raw.compute.health_check_matcher, "200")

  # Compute
  cpu           = try(local.raw.compute.cpu, 512)
  memory        = try(local.raw.compute.memory, 1024)
  desired_count = try(local.raw.compute.desired_count, 1)

  # Routing (required — domain is a convenience alias for routing.host)
  _routing_host = try(local.raw.routing.host, try(local.raw.domain, null))
  _routing_priority = try(local.raw.routing.priority, null)
  _validate_routing = (
    local._routing_host != null &&
    local._routing_priority != null
  ) ? true : tobool("app.yaml must define routing.host (or domain) and routing.priority")
  host     = local._routing_host
  priority = local._routing_priority

  # Resources (empty defaults)
  buckets   = try(local.raw.resources.buckets, [])
  databases = try(local.raw.resources.databases, [])
  secrets   = try(local.raw.resources.secrets, [])
  queues    = try(local.raw.resources.queues, [])

  # Alarms
  alarms_enabled      = try(local.raw.alarms.enabled, true)
  cpu_threshold       = try(local.raw.alarms.cpu_threshold, 80)
  memory_threshold    = try(local.raw.alarms.memory_threshold, 80)
  error_5xx_threshold = try(local.raw.alarms.error_5xx_threshold, 10)

  # Convert lists → maps keyed by name (stable for_each keys)
  bucket_map   = { for b in local.buckets : b.name => b }
  database_map = { for d in local.databases : d.name => d }
  secret_map   = { for s in local.secrets : s.name => s }
  queue_map    = { for q in local.queues : q.name => q }

  # Auto-wired secret env vars (YAML secrets → ECS secrets map)
  # Secrets with env field are injected into the ECS task definition
  yaml_secrets_map = {
    for s in local.secrets : s.env => module.secrets[s.name].secret_arn
    if try(s.env, "") != ""
  }
  all_secrets = merge(local.yaml_secrets_map, var.additional_secrets)

  # Auto-wired resource env vars (resources with env field → container env)
  bucket_env = {
    for b in local.buckets : b.env => module.buckets[b.name].bucket_name
    if try(b.env, "") != ""
  }
  database_env = {
    for d in local.databases : d.env => module.databases[d.name].table_name
    if try(d.env, "") != ""
  }
  queue_env = {
    for q in local.queues : q.env => module.queues[q.name].queue_url
    if try(q.env, "") != ""
  }

  yaml_environment = try(local.raw.environment, {})
  all_environment = merge(
    local.yaml_environment,
    local.bucket_env,
    local.database_env,
    local.queue_env,
    var.additional_environment,
  )

  # Auto-composed IAM policies from all resources (keyed map for stable for_each)
  all_policy_jsons = merge(
    { for name, _ in local.bucket_map : "bucket-${name}" => module.buckets[name].access_policy_json },
    { for name, _ in local.database_map : "database-${name}" => module.databases[name].access_policy_json },
    { for name, _ in local.secret_map : "secret-${name}" => module.secrets[name].access_policy_json },
    { for name, _ in local.queue_map : "queue-${name}" => module.queues[name].access_policy_json },
    var.additional_policy_jsons,
  )
}

################################################################################
# 1. Platform Data (discover shared infra)
################################################################################

module "platform" {
  source = "../platform-data"
}

################################################################################
# 2. ECR Repository
################################################################################

module "ecr" {
  source = "../ecr-repo"
  name   = local.name
}

################################################################################
# 3. ALB Routing (target group + listener rule + DNS)
################################################################################

module "routing" {
  source = "../service-routing"

  name                   = local.name
  project                = var.project
  vpc_id                 = module.platform.vpc_id
  port                   = local.port
  health_check_path      = local.health_check
  health_check_matcher   = local.health_check_matcher
  https_listener_arn     = module.platform.https_listener_arn
  listener_rule_priority = local.priority
  host_header            = local.host
  route53_zone_id        = module.platform.route53_zone_id
  alb_dns_name           = module.platform.alb_dns_name
  alb_zone_id            = module.platform.alb_zone_id
}

################################################################################
# 4. S3 Buckets
################################################################################

module "buckets" {
  source   = "../service-bucket"
  for_each = local.bucket_map

  # project=appname + name=resource → bucket: moresleep-data-{account_id}
  name           = each.key
  project        = local.name
  aws_account_id = var.aws_account_id
  service        = local.name
  versioning     = try(each.value.versioning, true)
  expire_days    = try(each.value.expire_days, 0)
}

################################################################################
# 5. DynamoDB Tables
################################################################################

module "databases" {
  source   = "../service-database"
  for_each = local.database_map

  # project=appname + name=resource → table: moresleep-sessions
  name           = each.key
  project        = local.name
  service        = local.name
  hash_key       = try(each.value.hash_key, "id")
  hash_key_type  = try(each.value.hash_key_type, "S")
  range_key      = try(each.value.range_key, null)
  range_key_type = try(each.value.range_key_type, "S")
  ttl_attribute  = try(each.value.ttl_attribute, "")
}

################################################################################
# 6. Secrets Manager
################################################################################

module "secrets" {
  source   = "../service-secret"
  for_each = local.secret_map

  # project=appname + name=resource → secret: moresleep/slack-bot-token
  name        = each.key
  project     = local.name
  service     = local.name
  description = try(each.value.description, "")
}

################################################################################
# 7. SQS Queues
################################################################################

module "queues" {
  source   = "../service-queue"
  for_each = local.queue_map

  # project=appname + name=resource → queue: moresleep-tasks
  name                       = each.key
  project                    = local.name
  service                    = local.name
  visibility_timeout_seconds = try(each.value.visibility_timeout, 30)
  retention_seconds          = try(each.value.retention_seconds, 345600)
  max_receive_count          = try(each.value.max_receive_count, 3)
}

################################################################################
# 8. IAM Task Role (auto-composed policies)
################################################################################

module "task_role" {
  source = "../service-role"

  name                    = local.name
  project                 = var.project
  region                  = var.region
  aws_account_id          = var.aws_account_id
  additional_policy_jsons = local.all_policy_jsons
}

################################################################################
# 9. ECS Fargate Service
################################################################################

module "service" {
  source = "../ecs-service"

  name              = local.name
  cluster_id        = module.platform.ecs_cluster_id
  image             = "${module.ecr.repository_url}:${var.image_tag}"
  cpu               = local.cpu
  memory            = local.memory
  port              = local.port
  health_check_path = local.health_check
  desired_count     = local.desired_count

  execution_role_arn = module.platform.execution_role_arn
  task_role_arn      = module.task_role.role_arn
  subnet_ids         = module.platform.private_subnet_ids
  security_group_ids = [module.platform.ecs_tasks_security_group_id]
  target_group_arn   = module.routing.target_group_arn
  region             = var.region

  environment = local.all_environment
  secrets     = local.all_secrets
}

################################################################################
# 10. CloudWatch Alarms (optional)
################################################################################

module "alarms" {
  source = "../service-alarm"
  count  = local.alarms_enabled ? 1 : 0

  name                    = local.name
  project                 = var.project
  service                 = local.name
  cluster_name            = module.platform.ecs_cluster_name
  sns_topic_arns          = [data.aws_sns_topic.alerts.arn]
  cpu_threshold           = local.cpu_threshold
  memory_threshold        = local.memory_threshold
  error_5xx_threshold     = local.error_5xx_threshold
  target_group_arn_suffix = module.routing.target_group_arn_suffix
  alb_arn_suffix          = module.platform.alb_arn_suffix
}

data "aws_sns_topic" "alerts" {
  name = "${var.project}-alerts"
}
