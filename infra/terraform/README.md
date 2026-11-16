# Infrastructure stub: AWS Mumbai

A starting point for running Revlane in `ap-south-1`. It has never been applied or validated. Read `docs/deployment.md` first, in particular "Before the first deployment".

What it describes: a VPC with private subnets, an encrypted Postgres 16 instance, a private encrypted bucket for drafted documents, a KMS key, Secrets Manager entries, an ECS cluster with API, worker and web services, and a daily retention schedule.

What it leaves out on purpose, marked `TODO` in the code: the load balancer and its certificate, the task roles' policies, and the ECR repositories.

Nothing here holds a secret. Values come from variables; `terraform.tfvars` is ignored by git.
