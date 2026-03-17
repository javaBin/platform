################################################################################
# Permission Boundary: javabin-developer-boundary (data source)
#
# The boundary resource lives in terraform/org/boundary.tf (human-applied)
# because the boundary's self-protection prevents CI from modifying it.
# This data source reads the existing policy so platform resources can
# reference its ARN.
################################################################################

data "aws_iam_policy" "developer_boundary" {
  name = "${var.project}-developer-boundary"
}
