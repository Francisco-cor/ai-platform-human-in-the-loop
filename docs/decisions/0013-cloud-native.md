# ADR 0013 — Cloud Native, GitOps y SRE (Fase 10)

**Fecha:** 2026-09-02
**Estado:** Aceptada
**Fase:** 10 Elevación — Cloud native, GitOps y SRE
**Relacionada:** PLAN_ELEVACION_11_FASES.md Fase 10 §10, commits F10-1..F10-7

## Contexto

Fases 0–9 entregaron plataforma local operable con `docker compose` (PG+pgvector HNSW, Redis, API, worker, fake Agent Station), 299 tests, RAG HNSW+hybrid+reranker, LLM fallback, approval durable, HITL UI, API Platform SDK/webhooks, Data Platform BQ+GCS+time-travel+lineage+flags+retention. Gap para staging productivo: sin IaC reproducible (`infra/terraform/main.tf` placeholder), sin CI de imagen (`ci.yml` solo lint+pytest), sin canary `10%` ni `blue/green`, sin migrate job `alembic` con `PGSSLCERT`, sin runbooks/SLO ni chaos `toxiproxy`, sin backup drill, sin rotación secrets con Workload Identity (aún `GOOGLE_APPLICATION_CREDENTIALS` key file en docs).

Criterio Fase 10: `terraform apply -target=staging` idempotente, `git push main` → staging en `<15m`, `make smoke-staging` `202→AWAITING→approved→COMPLETED` con trace, `alembic downgrade -1 && upgrade head` sin downtime, backup restaura y datos verificados, canary `10%` sin error, rollback `<5m`.

## Decisión

### F10-1 Terraform módulos (`infra/terraform/modules/*`, `envs/staging|prod`)

- **Módulos 7** cada uno `variables.tf/main.tf/outputs.tf`:
  - `cloud_run` (`google_cloud_run_v2_service` con `scaling 1-10`, `cpu 1, memory 512Mi`, `startup/liveness probe /healthz`, `service_account` WIF, `traffic 100 latest`, `google_cloud_run_service_iam_member public allUsers`).
  - `cloud_sql` (`google_sql_database_instance` PG16 `db-g1-small` staging / `db-custom-1-3840` prod, `deletion_protection false/true`, `backup_configuration PITR 7d, retained 7`, `google_sql_database` + `random_password`).
  - `redis` (`google_redis_instance` `REDIS_7_0` `BASIC 1GB` staging / `5GB` prod).
  - `gcs` (`google_storage_bucket` for_each `buckets={docs,evals,artifacts}` con `versioning true`, `lifecycle_rule Delete age 365/90/30`, `uniform_bucket_level_access`).
  - `bq` (`google_bigquery_dataset` `procurement_ops/evals/finops` + `google_bigquery_table` `audit/reports/cost` con JSON schemas).
  - `iam` (`google_service_account procurement-api`, `google_project_iam_member` bindings `run.invoker, cloudsql.client, storage.objectAdmin, bigquery.dataEditor, secretmanager.secretAccessor`, `google_service_account_iam_member workloadIdentityUser` para `default/procurement-api`).
  - `secrets` (`google_secret_manager_secret` for_each `gemini_api_key, deepseek_api_key, db_password, jwt_secret` con `rotation {period 2592000s (30d), next_rotation_time timeadd(timestamp, 720h)}`, `google_secret_manager_secret_iam_member` + `google_secret_manager_secret_version` initial `changeme` lifecycle `ignore_changes secret_data`).
- **Envs:** `infra/terraform/envs/staging/main.tf` compone `google_project_service` (`run, sqladmin, redis, storage, bigquery, secretmanager, artifactregistry`), `google_artifact_registry_repository procurement DOCKER`, módulos `iam, secrets, gcs, bq, cloud_sql, redis, cloud_run` con `backend gcs bucket procurement-tf-state-staging prefix terraform/state` y `env_vars` (`PROCUREMENT_APP_ENV=staging`, `DATABASE_URL` via `module.cloud_sql`, `REDIS_URL`, `GCS_BUCKET`, `BIGQUERY_DATASET`, `OTEL_EXPORTER=otlp`). `prod/main.tf` idéntico con `tier db-custom-1-3840` y `min 2 max 20` + `deletion_protection true`.
- **Validación:** `make terraform-validate` (`terraform fmt -check -recursive`, `terraform init -backend=false && validate` staging+prod), `tflint --recursive` (CI job `terraform-plan`).

### F10-2 CI/CD (` .github/workflows/cd.yml`, `Makefile:docker-build`)

- **Workflow `cd.yml`** jobs:
  - `build` (`docker/setup-buildx`, `buildx build --cache-from gha --cache-to gha,mode=max --build-arg VERSION=$(git rev-parse --short HEAD) --tag image:sha --tag staging-latest --load`).
  - `scan` (`aquasecurity/trivy-action` `severity CRITICAL,HIGH exit-code 1`, `pip-audit`, `anchore/sbom-action` `syft` `cyclonedx-json` `sbom.json` artifact).
  - `terraform-plan` (`hashicorp/setup-terraform 1.6`, `terraform fmt`, `init -backend=false && validate`, `tflint`).
  - `scan-push` (needs `build, terraform-plan`, `if: main`, `google-github-actions/auth` `workload_identity_provider` + `service_account`, `gcloud auth configure-docker`, `docker push`).
  - `deploy-staging` (needs `scan-push`, `environment: staging`, `infra/deploy/cloud_run_deploy.sh staging $IMAGE`, `curl /readyz` 10s, `make smoke-staging`).
  - `deploy-prod` (needs `deploy-staging`, `if: tag v*`, `environment: prod` manual approval, `cloud_run_deploy.sh prod $IMAGE` canary 10% → mensaje `promotion to 100% manual`).
- **Makefile** add `terraform-validate`, `terraform-plan`, `tflint`, `docker-build` (`docker buildx build --build-arg VERSION=${VERSION:-0.1.0} --tag procurement-platform:local`), `docker-push`, `sbom` (`syft`), `backup-drill`, `backup-create`, `migrate-job`, `pgbouncer-config`, `chaos-test`, `slo-check`, `runbooks`.

### F10-3 Deploy canary (`infra/deploy/cloud_run_deploy.sh`)

- Script `staging` → `gcloud run deploy $SERVICE --image $IMAGE --region $REGION --project $PROJECT --allow-unauthenticated --service-account procurement-api@$PROJECT.iam.gserviceaccount.com --set-env-vars PROCUREMENT_APP_ENV=staging --quiet` luego `gcloud run services describe --format value(status.url)` + `curl /readyz` (mock si no creds). `prod` → `--traffic 90=stable,10=canary --no-allow-unauthenticated` + canary health `curl /readyz` + mensaje promoción `gcloud run services update-traffic --to-latest`. Rollback `<5m` via `gcloud run services update-traffic --to-revisions PREV=100`. WIF es usado, no `GCP_SA_KEY`.

### F10-4 DB migrate + pgbouncer (`infra/db/migrate_job.yaml`, `pgbouncer.ini`)

- **Job** `batch/v1` `procurement-migrate` `ttlSecondsAfterFinished 3600`, `serviceAccountName procurement-migrate` con `iam.gke.io/gcp-service-account` WIF, `containers.migrate image staging-latest command ["alembic", "upgrade", "head"] env PROCUREMENT_DATABASE_URL secretKeyRef procurement-db-url + PGSSLCERT /etc/ssl/certs/ca-certificates.crt volume ssl-certs`. `terminationGracePeriodSeconds 30`.
- **Pgbouncer** `listen_port 6432 pool_mode transaction max_client_conn 1000 default_pool_size 20 reserve_pool_size 5`, `auth_type md5`, `server_reset_query DISCARD ALL`. App conecta via `postgresql+psycopg://procurement:pass@pgbouncer:6432/procurement` (Cloud SQL Auth Proxy delante).
- Test `tests/chaos/test_db_failover.py::test_db_migration_idempotent` verifica `Base.metadata.create_all` idempotente + `alembic downgrade -1 && upgrade head` en staging.

### F10-5 SRE runbooks, SLO, chaos (`docs/operations/*`, `observability/alerts/alerts.yaml`, `tests/chaos`)

- **SLO** `docs/operations/SLO.md` 99.9% availability 28d (burn 5), p95 <1s, eval >95%, backlog <50, unsafe 0, duplicate 0 (ventana 5m, alertmanager).
- **Runbooks** 7: `approval-stuck.md` (backlog >50 → `approval_pending_total`, `scope_hash`), `pgvector-slow.md` (RAG p95 >200ms → `EXPLAIN ANALYZE`, `hnsw`), `redis-down.md` (readyz degraded → `gcloud redis instances describe`, fallback memory), `llm-timeout.md` (fallback Gemini→DeepSeek→Fake, `was_fallback`, `max_tokens`), `trace-not-found.md`, `budget-exceeded.md`, `db-failover.md` (Cloud SQL failover, `pgbouncer SHOW STATS`, chaos `toxiproxy`).
- **Chaos** `tests/chaos/test_db_failover.py` 3 tests: `test_db_failover_no_duplicate_via_toxiproxy_mock` (monkeypatch `OperationalError` blackhole 5s → `resume_durable` idempotente `submit 1`), `test_db_migration_idempotent`, `test_redis_down_fallback_to_memory` (invalid `REDIS_URL` → `MemoryLockManager`).
- **Alert** `observability/alerts/alerts.yaml` ya tenía `Http5xxHigh, P95LatencyHigh, ApprovalBacklogHigh, BudgetExceededHigh`; Fase 10 añade `RedisDown, DBDown` (ver runbooks).

### F10-6 Backup drill (`infra/backup/backup.sh`, `docs/operations/restore_drill.md`)

- Script `create` (`gcloud sql backups create --instance procurement-pg --async`), `list`, `restore-drill` (create temp instance `procurement-pg-restore-drill-<ts>` from latest backup, `alembic upgrade head`, `pytest -q`, `SELECT count(*) FROM audit_events`, `gcloud sql instances delete` TTL 1h). Cron `Cloud Scheduler 0 3 * * *` daily.
- Drill doc `docs/operations/restore_drill.md` cron + CI mensual `.github/workflows/cd.yml` job `backup-drill` (`infra/backup/backup.sh restore-drill`). RTO 5m, RPO <1m (PITR 7d, bucket versioning).

### F10-7 Secrets rotation + workload identity (`infra/terraform/modules/secrets`, `src/procurement_platform/security/secrets_rotation.py`, `api/main.py:/v1/secrets`)

- **Terraform** rotation `30d` via `rotation {rotation_period = 2592000s}` + `google_service_account_iam_member workloadIdentityUser`.
- **App** `security/secrets_rotation.py:is_workload_identity_enabled()` verifica `GOOGLE_APPLICATION_CREDENTIALS` no es `service_account` key (type `external_account` WIF ok), `emit_secret_rotation_audit(db, secret_id, actor_id, trace_id)` crea `audit_events event_type secret.rotation` con `rotation_period_days 30` y `workload_identity`. API `POST /v1/secrets/{id}/rotate` (admin) + `GET /v1/secrets/rotation/status`.
- **CI** `.github/workflows/cd.yml` usa `google-github-actions/auth` WIF `workload_identity_provider` + `service_account`, no `GCP_SA_KEY`.

## Consecuencias

- Suite `pytest -q` 299→310+ (26 nuevos F10 tests) <220s, `terraform validate` mock pasa sin creds (`init -backend=false`), `tflint` no bloquea demo.
- `git push main` → staging <15m (build 2m + scan 1m + terraform plan 1m + deploy 1m + smoke 30s), canary `10%` prod manual → `gcloud run services update-traffic --to-latest` `<5m` rollback.
- `make terraform-validate && make docker-build VERSION=staging-$(git rev-parse --short HEAD) && make backup-drill` (mock) pasa local sin `gcloud` (fallback `echo mock`).
- `pytest tests/chaos -m chaos` valida DB failover sin duplicate (idempotencia Redis+PG).
- Secrets sin key file: `GET /v1/secrets/rotation/status` returns `workload_identity true` local.

## Alternativas descartadas

- **GKE vs Cloud Run Jobs** para migrate: Cloud Run `v2` elegido por simplicidad; GKE `Job` YAML incluido como alternativa pero default es `gcloud run jobs create` (comentado en `migrate_job.yaml`).
- **Cloud SQL Auth Proxy vs private IP**: Proxy con `pgbouncer` elegido para TLS `PGSSLCERT`; private VPC no necesario para staging demo.
- **Velero backup vs gcloud sql backups**: `gcloud sql backups create` elegido por nativo PG backup + PITR; Velero para GKE volumes es P1.
- **Vault vs Secret Manager**: Secret Manager nativo con rotation 30d elegido; Vault es overkill para MVP.
