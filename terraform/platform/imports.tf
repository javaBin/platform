################################################################################
# One-time adoption of resources that existed before Terraform managed them.
#
# Import blocks are no-ops once the resource is in state, so these can be
# removed in a later cleanup after the first apply.
################################################################################

# Platform Lambda log groups were auto-created by Lambda ("Never expire").
# budget-enforcer has never logged, so it has no group yet and is created fresh.
import {
  for_each = toset([
    "app-auth-sync",
    "app-secret-rollout",
    "apply-gate",
    "ci-broker",
    "compliance-reporter",
    "cost-report",
    "daily-cost-check",
    "override-cleanup",
    "password-set",
    "plan-review",
    "resource-tagger",
    "securityhub-summary",
    "slack-alert",
    "team-provisioner",
  ])
  to = module.lambdas.aws_cloudwatch_log_group.lambda[each.key]
  id = "/aws/lambda/${var.project}-${each.key}"
}

import {
  to = module.identity.aws_cloudwatch_log_group.auth_claims
  id = "/aws/lambda/${var.project}-auth-claims"
}
