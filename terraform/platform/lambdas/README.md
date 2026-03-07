# Lambdas Module

Terraform for all platform Lambda functions. Source code lives in `terraform/lambda-src/`.

## Resources Created

| Lambda | Runtime | Trigger | Bedrock |
|--------|---------|---------|---------|
| `javabin-slack-alert` | Python 3.12 | SNS (both topics) | Yes |
| `javabin-cost-report` | Python 3.12 | EventBridge (Monday 08:00) | Yes |
| `javabin-daily-cost-check` | Python 3.12 | EventBridge (daily 08:00) | No |
| `javabin-compliance-reporter` | Python 3.12 | EventBridge (resource creation) | No |
| `javabin-override-cleanup` | Python 3.12 | EventBridge (hourly) | No |
| `javabin-team-provisioner` | Python 3.12 | (future) registry events | No |

Each Lambda has its own IAM role with least-privilege policies.

## Variables

| Name | Description |
|------|-------------|
| `project` | Resource naming prefix |
| `region` | AWS region |
| `aws_account_id` | Account ID for ARN construction |
| `alerts_topic_arn` | SNS topic for cost alerts |
| `security_topic_arn` | SNS topic for security alerts |
| `compliance_reporter_identities` | IAM identity substrings for compliance filtering |

## Outputs

All 6 function ARNs are exported.

## Lambda Packaging

Source code is packaged via `archive_file` data sources. Zip files are build artifacts (in `.gitignore`). The `shared/` module is included in the `slack_alert` zip via `source_dir` pointing to the parent `lambda-src/` with appropriate path structure.
