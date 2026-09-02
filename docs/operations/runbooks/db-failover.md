# Runbook — Cloud SQL failover / DB degraded (F10)

**Alerta:** `DBDown` o `readyz` `status: degraded` + `checks.db: degraded`, `pg_isready` fail, `sqlalchemy.exc.OperationalError`.

## Diagnóstico
1. `curl /readyz` → `checks.db` debe ser `ok`. Si `degraded`, revisar `docker compose logs postgres` o `gcloud sql instances describe procurement-pg --project ...`.
2. `curl /healthz` → `status: ready` solo si app viva pero DB no necesaria para liveness.
3. `alembic current` → debe ser `head`; si no, `infra/db/migrate_job.yaml` no corrió.
4. `GET /v1/procurement/executions?limit=1` → 500 si DB down.

## Causas
- Cloud SQL maintenance, `pgvector` extension no habilitada, `statement_timeout 5s` excedido, disk full.
- `pgbouncer.ini` `max_db_connections 20` saturado.
- Network partition VPC.

## Acciones
- **Local:** `docker compose restart postgres` + `alembic upgrade head`.
- **Staging:** `gcloud sql instances describe procurement-pg` → `state: RUNNABLE`; si `MAINTENANCE`, esperar. Si `FAILED`, `gcloud sql instances failover procurement-pg --project ...` (ZONAL → no replica, recreate via `terraform apply -target=module.cloud_sql`).
- **Migrate:** `kubectl apply -f infra/db/migrate_job.yaml` + `kubectl logs job/procurement-migrate`. Si falla, `alembic downgrade -1 && alembic upgrade head` en staging con `PGSSLCERT`.
- **Pgbouncer:** `docker logs pgbouncer` → `SHOW STATS`; si `avg_xact_time >500ms`, aumentar `default_pool_size`.
- **Chaos test:** `pytest tests/chaos/test_db_failover.py -v -m chaos` usa `toxiproxy` para simular `blackhole` 5s y verifica `resume_durable` no duplica `order_id` (idempotencia Redis+PG).
- **Prevención:** dashboard `Postgres QPS` + alert `pg_slow_query >1s`; backup diario 7d retention (ver `infra/backup/backup.sh`).

**RTO:** 5m (Cloud SQL point-in-time recovery + pgbouncer). **RPO:** <1m (WAL 7d).
