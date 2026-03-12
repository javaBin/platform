# One-time import: ci-infra-plan role was pre-created via CLI to bootstrap
# the plan job. Remove this file after the first successful apply.
import {
  to = module.iam.aws_iam_role.ci_infra_plan
  id = "javabin-ci-infra-plan"
}
