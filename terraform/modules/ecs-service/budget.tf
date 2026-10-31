################################################################################
# Budget suspension
#
# The budget-enforcer Lambda scales a team's services to 0 when it passes 200%
# of its monthly budget, sets their javabin:desired-count tag to "0" and writes
# {budget_suspension_path}/{team}. Planning the tag as "0" while that marker
# exists keeps the next apply from writing compute.desired_count back, so the
# ecs-deploy after it (which scales to the tag) keeps 0 tasks. The Lambda
# deletes the marker and restores the count and tag when the suspension is
# lifted (docs/cost-management.md).
#
# By path, not a single-parameter lookup: a missing marker returns an empty
# list instead of failing the plan.
################################################################################

data "aws_ssm_parameters_by_path" "budget_suspension" {
  path            = var.budget_suspension_path
  with_decryption = false
}

locals {
  budget_suspended = contains(data.aws_ssm_parameters_by_path.budget_suspension.names, "${trimsuffix(var.budget_suspension_path, "/")}/${var.team}")
}
