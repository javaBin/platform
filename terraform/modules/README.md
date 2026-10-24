# Reusable Terraform Modules

App repos source these modules via `git::` URLs:

```hcl
module "app" {
  source = "git::https://github.com/javaBin/platform.git//terraform/modules/app-stack?ref=main"
  config_file    = "${path.root}/../app.yaml"
  aws_account_id = "553637109631"
}
```

## Modules

| Module | Purpose | Key Output |
|--------|---------|------------|
| [platform-data](platform-data/) | Discover shared infra (VPC, ALB, ECS, Route53) | `vpc_id`, `ecs_cluster_id`, `https_listener_arn` |
| [ecr-repo](ecr-repo/) | ECR repository + lifecycle policy | `repository_url` |
| [service-routing](service-routing/) | ALB target group + listener rule + DNS | `target_group_arn`, `dns_name` |
| [service-role](service-role/) | ECS task IAM role + composable policies | `role_arn` |
| [ecs-service](ecs-service/) | ECS Fargate service + task def + logs | `service_name` |
| [service-bucket](service-bucket/) | S3 bucket with standard security config | `bucket_name`, `access_policy_json` |
| [service-database](service-database/) | DynamoDB table | `table_name`, `access_policy_json` |
| [service-rds](service-rds/) | RDS PostgreSQL instance | `endpoint`, `address`, `port` |
| [service-rds-adopted](service-rds-adopted/) | Existing RDS instance imported as-is | `endpoint`, `address`, `port` |
| [service-secret](service-secret/) | SSM Parameter Store SecureString | `parameter_arn`, `access_policy_json` |
| [service-queue](service-queue/) | SQS queue + DLQ | `queue_url`, `access_policy_json` |
| [service-alarm](service-alarm/) | CloudWatch alarms for ECS | `cpu_alarm_arn` |
| [app-stack](app-stack/) | Golden path — reads `app.yaml`, creates everything | All of the above |
| [cognito-app-client](cognito-app-client/) | Requests a Cognito client (created by the app-auth-sync Lambda) | `issuer_url`, `client_id_arn`, `client_secret_arn` |

## Golden Path

Most apps should use `app-stack` only. It reads `app.yaml` from the repo root and auto-creates all needed infrastructure. See [docs/reusable-modules.md](../../docs/reusable-modules.md) for the full `app.yaml` schema.

## Policy Composition

Resource modules (`service-bucket`, `service-database`, `service-secret`, `service-queue`) export `access_policy_json`. Pass these to `service-role` via `additional_policy_jsons` to compose least-privilege IAM. `scripts/generate-modules.py` does this automatically.
