################################################################################
# Root module — wires together all platform sub-modules
#
# This is the CI-applied platform module. Push to main triggers:
#   GitHub Actions → plan → LLM review → apply
#
# Sub-modules:
#   networking  VPC, subnets, NAT, security groups
#   ingress     ALB, ACM wildcard cert, Route53 DNS
#   iam         GitHub OIDC, CI roles, permission boundary, ECS execution role
#   compute     ECS cluster, public ECR images
#   monitoring  SNS topics, EventBridge rules, Config, GuardDuty, Security Hub
#   lambdas     slack-alert, cost-report, daily-cost-check, auto-tagger
#   identity    Cognito pools (Identity Center is in terraform/org/)
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
}

module "iam" {
  source               = "./iam"
  project              = var.project
  region               = var.region
  aws_account_id       = var.aws_account_id
  registered_app_repos = var.registered_app_repos
  override_approvers   = var.override_approvers
  github_org           = var.github_org
  apply_gate_role_arn  = module.lambdas.apply_gate_role_arn
}

module "compute" {
  source  = "./compute"
  project = var.project
  region  = var.region
}

module "monitoring" {
  source         = "./monitoring"
  project        = var.project
  region         = var.region
  aws_account_id = var.aws_account_id
}

module "lambdas" {
  source                         = "./lambdas"
  project                        = var.project
  region                         = var.region
  aws_account_id                 = var.aws_account_id
  alerts_topic_arn               = module.monitoring.alerts_topic_arn
  security_topic_arn             = module.monitoring.security_topic_arn
  compliance_reporter_identities = var.auto_tagger_identities
  internal_user_pool_id          = module.identity.internal_user_pool_id
  internal_user_pool_arn         = module.identity.internal_user_pool_arn
  identity_store_id              = var.identity_store_id
  sso_instance_arn               = var.sso_instance_arn
  alert_dedup_table_name         = module.monitoring.alert_dedup_table_name
  alert_dedup_table_arn          = module.monitoring.alert_dedup_table_arn
}

module "identity" {
  source               = "./identity"
  project              = var.project
  domain               = var.domain
  route53_zone_id      = module.ingress.route53_zone_id
  google_client_id     = var.google_client_id
  google_client_secret = var.google_client_secret
  certificate_arn      = var.certificate_arn
}
