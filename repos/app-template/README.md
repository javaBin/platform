# My Service

A Javabin platform service.

## Getting Started

1. **Create from template**: Click "Use this template" on GitHub
2. **Register your app**: Open a PR to [javaBin/registry](https://github.com/javaBin/registry) adding `apps/your-service.yaml`
3. **Customize `app.yaml`**: Set your service name, team, compute, and routing
4. **Push to main**: The platform CI pipeline builds, plans, reviews, and deploys automatically

## Platform Features

- Automatic Docker build and ECR push
- Terraform infrastructure from `app.yaml` (S3, DynamoDB, SQS, Secrets Manager)
- LLM-powered plan review with risk gating
- ECS Fargate deployment with health checks
- CloudWatch alarms and cost monitoring

## References

- [app.yaml reference](https://github.com/javaBin/platform/blob/main/docs/app-yaml-reference.md)
- [Platform documentation](https://github.com/javaBin/platform)
- [Registry](https://github.com/javaBin/registry)
