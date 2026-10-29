################################################################################
# Root module — wires together all platform sub-modules
#
# This is the CI-applied platform module. Push to main triggers:
#   GitHub Actions → plan → LLM review → apply
#
# Sub-modules:
#   networking  VPC, subnets, NAT, security groups, app-to-app DNS namespace
#   ingress     ALB, ACM wildcard certs (*.javazone.no, *.java.no, *.javabin.no)
#   iam         GitHub OIDC, CI roles, permission boundary, ECS execution role
#   compute     ECS cluster, public ECR images
#   monitoring       SNS topics, EventBridge rules, Config, GuardDuty, Security Hub
#   cost-analytics   CUR, Athena, Glue, account budget
#   lambdas          slack-alert, cost-report, daily-cost-check, auto-tagger
#   identity         Cognito pools (Identity Center is in terraform/org/)
#   dns              Route53 hosted zones for java.no, javabin.no/com, teknologihuset.no
################################################################################

module "networking" {
  source  = "./networking"
  project = var.project
  region  = var.region
}

module "ingress" {
  source            = "./ingress"
  project           = var.project
  domain            = var.domain
  public_subnet_ids = module.networking.public_subnet_ids
  alb_sg_id         = module.networking.alb_sg_id

  extra_domains = {
    "java.no"    = module.dns.java_no_zone_id
    "javabin.no" = module.dns.javabin_no_zone_id
  }
}

module "iam" {
  source                   = "./iam"
  project                  = var.project
  region                   = var.region
  aws_account_id           = var.aws_account_id
  registered_teams         = var.registered_teams
  github_org               = var.github_org
  apply_gate_role_arn      = module.lambdas.apply_gate_role_arn
  ci_broker_role_arn       = module.lambdas.ci_broker_role_arn
  ci_broker_function_arn   = module.lambdas.ci_broker_function_arn
  plan_review_function_arn = module.lambdas.plan_review_function_arn
}

module "compute" {
  source  = "./compute"
  project = var.project
  region  = var.region
}

module "monitoring" {
  source                = "./monitoring"
  project               = var.project
  region                = var.region
  aws_account_id        = var.aws_account_id
  alb_http_listener_arn = module.ingress.http_listener_arn
}

module "cost_analytics" {
  source             = "./cost-analytics"
  project            = var.project
  region             = var.region
  aws_account_id     = var.aws_account_id
  alerts_topic_arn   = module.monitoring.alerts_topic_arn
  org_boundary_arn   = module.iam.org_boundary_arn
  account_budget_usd = 600

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }
}

module "lambdas" {
  source                         = "./lambdas"
  project                        = var.project
  region                         = var.region
  aws_account_id                 = var.aws_account_id
  domain                         = var.domain
  alerts_topic_arn               = module.monitoring.alerts_topic_arn
  security_topic_arn             = module.monitoring.security_topic_arn
  compliance_reporter_identities = var.auto_tagger_identities
  internal_user_pool_id          = module.identity.internal_user_pool_id
  internal_user_pool_arn         = module.identity.internal_user_pool_arn
  external_user_pool_arn         = module.identity.external_user_pool_arn
  auth_memberships_table_name    = module.identity.auth_memberships_table_name
  auth_memberships_table_arn     = module.identity.auth_memberships_table_arn
  internal_identity_providers    = module.identity.internal_identity_providers
  external_identity_providers    = module.identity.external_identity_providers
  internal_user_pool_domain      = module.identity.internal_user_pool_domain
  external_user_pool_domain      = module.identity.external_user_pool_domain
  identity_store_id              = var.identity_store_id
  sso_instance_arn               = var.sso_instance_arn
  alert_dedup_table_name         = module.monitoring.alert_dedup_table_name
  alert_dedup_table_arn          = module.monitoring.alert_dedup_table_arn
  https_listener_arn             = module.ingress.https_listener_arn
  alb_dns_name                   = module.ingress.alb_dns_name
  alb_zone_id                    = module.ingress.alb_zone_id
  route53_zone_id                = module.ingress.route53_zone_id
  org_boundary_arn               = module.iam.org_boundary_arn
  developer_boundary_arn         = module.iam.developer_boundary_arn
  cur_glue_database              = module.cost_analytics.glue_database_name
  cur_glue_table                 = module.cost_analytics.glue_table_name
  athena_workgroup               = module.cost_analytics.athena_workgroup_name
  athena_results_bucket_arn      = module.cost_analytics.athena_results_bucket_arn
  cur_data_bucket_arn            = module.cost_analytics.cur_data_bucket_arn
  vpc_id                         = module.networking.vpc_id
  vpc_arn                        = module.networking.vpc_arn
  service_discovery_namespace_id = module.networking.service_discovery_namespace_id
  service_discovery_zone_id      = module.networking.service_discovery_hosted_zone_id
}

module "identity" {
  source               = "./identity"
  project              = var.project
  domain               = var.domain
  route53_zone_id      = module.ingress.route53_zone_id
  google_client_id     = var.google_client_id
  google_client_secret = var.google_client_secret
  certificate_arn      = var.certificate_arn
  org_boundary_arn     = module.iam.org_boundary_arn
}

module "dns" {
  source           = "./dns"
  project          = var.project
  region           = var.region
  javazone_zone_id = module.ingress.route53_zone_id

  providers = {
    aws           = aws
    aws.us_east_1 = aws.us_east_1
  }
}
