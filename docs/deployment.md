# Deployment guide

Revlane has not been deployed. This guide and the code in `infra/` describe how it is meant to run in one cloud region, AWS Mumbai (`ap-south-1`). The infrastructure code is a stub: it has never been applied, and the two Dockerfiles have never been built. Treat every step here as a plan to be verified.

## What runs

| Part | What it is | Where |
| --- | --- | --- |
| API | `uvicorn revlane.main:app` | ECS Fargate service behind a load balancer |
| Worker | `python -m revlane.harness worker` | ECS Fargate service, no inbound traffic |
| Web | `next start` | ECS Fargate service behind the same load balancer |
| Database | Postgres 16 | RDS, private subnets, encrypted |
| Files | Drafted claims and reports | S3 bucket, private, encrypted |
| Secrets | Database password, model key, hashing key | Secrets Manager, under a KMS key |
| Scheduled jobs | Retention | An EventBridge schedule running the worker image |

One database holds operational data, the analytics views and the job queue. There is no Kafka, Temporal, ClickHouse or model gateway; `docs/technical-design.md` says when each would be added.

## Before the first deployment

These are not built and block a real deployment:

1. **Onboarding a tenant.** Sign-in exists, but tenants and their first admin are created by hand (`python -m revlane.auth add ...` against a tenant row you insert). There is no self-service sign-up, password reset, or user-management screen.
2. **A production vault backend.** `revlane/vault/` has only the development backend, which encrypts with a key from the environment. Write a Secrets Manager backend behind the same interface.
3. **An S3 storage backend.** `revlane/storage/` has only the local-folder backend.
4. **Shared rate limits.** The limiter counts in process memory. With more than one API task, move the counters to a shared store or to the load balancer.
5. **A model key with zero-retention terms**, if any real merchant data will reach a model.

## Steps

1. **Build and push the images.** `apps/api/Dockerfile` and `apps/web/Dockerfile`, to two ECR repositories. Build the web image with `NEXT_PUBLIC_API_URL` set to the public API address, because Next.js fixes it at build time.
2. **Create the infrastructure.** In `infra/terraform/`, copy `terraform.tfvars.example`, fill it in, then `terraform init`, `terraform plan`, and read the plan before `terraform apply`.
3. **Create the database roles.** Migrations need `revlane_app` and `revlane_chat` to exist, or a migrating user allowed to create them. On RDS the master user can; run migrations as that user or create the two roles first.
4. **Run migrations** as a one-off task from the API image: `alembic upgrade head`.
5. **Set the environment** on the API and worker tasks from Secrets Manager: `DATABASE_URL`, `TOKEN_HASH_ROOT_KEY`, `VAULT_KEY`, `LLM_API_KEY`. Set `LLM_STUB=false` only when a key is present. Leave `PROVIDER_LIVE_CALLS=false`.
6. **Set `WEB_ORIGINS`** on the API to the console's address. Serve the console and the API from the same site (for example `app.` and `api.` under one domain), or the browser will not send the session cookie.
7. **Set sign-in up.** `SESSION_SECRET` to a long random value from Secrets Manager, `SESSION_COOKIE_SECURE=true`, and leave `DEMO_USER_PASSWORD` empty so the demo users cannot sign in. Set `METRICS_TOKEN` if you scrape metrics.
8. **Schedule retention** daily: the worker image with the command `python -m revlane.lifecycle retention`.
9. **Check it.** `GET /api/v1/health` should report the database as `ok`; `GET /api/v1/metrics` should answer; the console should load and show the first-run steps.

## Settings that matter in production

| Setting | Value |
| --- | --- |
| `AGENT_RUNS_INLINE` | `false`: the worker service carries out runs |
| `AGENTS_ENABLED` | `true`; set to `false` to stop every agent at once |
| `PROVIDER_LIVE_CALLS` | `false` |
| `RATE_LIMIT_PER_MINUTE`, `RATE_LIMIT_CHAT_PER_MINUTE`, `RATE_LIMIT_UPLOADS_PER_MINUTE` | Defaults are 1200, 60 and 120 per client |
| `MAX_BODY_BYTES` | Default 1 MB for anything but an upload |

## Operating it

- **Backups.** RDS automated backups with point-in-time recovery; the stub sets seven days.
- **Keeping `TOKEN_HASH_ROOT_KEY`.** Shopper tokens are hashes under a key derived from it. If it is lost or changed, existing tokens no longer match new ones for the same shopper.
- **Rolling back.** Every migration has a downgrade. Roll the tasks back to the previous image first, then the migration.
- **Offboarding a tenant.** `python -m revlane.lifecycle offboard <tenant id>` from the worker image.
- **What to watch.** `revlane_sync_lag_seconds`, `revlane_unmapped_status_attempts`, runs ending `failed` or `escalated` in `revlane_agent_runs`, and `revlane_model_tokens_total`.
