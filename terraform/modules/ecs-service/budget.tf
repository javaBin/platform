################################################################################
# Budget suspension
#
# The budget-enforcer Lambda scales a team's services to 0 when it passes 200%
# of its monthly budget and writes {budget_suspension_path}/{team}. Planning
# desired_count=0 while that marker exists keeps the next apply from scaling
# the service back up. The Lambda deletes the marker and restores the count
# when the suspension is lifted (docs/cost-management.md).
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
