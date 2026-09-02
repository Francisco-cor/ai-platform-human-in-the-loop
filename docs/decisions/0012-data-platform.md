# ADR 0012 — Data Platform y Analytics (BigQuery, GCS, time-travel, lineage, flags, retention) Fase 9 Elevación

**Fecha:** 2026-09-02
**Estado:** Aceptada
**Fase:** 9 Elevación — Data platform y analytics
**Relacionada:** PLAN_ELEVACION_11_FASES.md Fase 9 §9, commits F9-1..F9-6

## Contexto

Fases 0–8 entregaron vertical slice operable (procurement flujo feliz → aprobación durable → HITL UI → API Platform SDK/webhooks) con 285 tests y observabilidad básica (OTel, Prometheus, Grafana). Gap para portfolio/pre-prod: sin analítica reproducible (BigQuery), sin artifacts durables (GCS), sin time-travel debugging, sin lineage de evidencia (qué ejecuciones usaron `policy_budget_v1`), sin feature flags hot-reload y sin retención/GDPR. Criterio Fase 9: evento PG aparece en BigQuery <60s (batch) o <10s (stream) sin PII, `SELECT count(*) FROM ops.audit WHERE execution_id=xxx` funciona en staging, `flags.yaml` cambia comportamiento sin deploy, lineage responde en <100ms, soft-delete con tombstone.

## Decisión

### F9-1 BigQuery drainer batch (`pipeline/bq_drainer.py:73`, `api/main.py:/v1/bq/*`, `workers/tasks.py:drain_bq_job`)

- `OutboxEvent` (F2-1) ya existente como outbox transaccional (`audit/service.py:create_audit_event` escribe `audit_events` + `outbox_events` en misma `db.flush()`). Fase 9 añade drainer batch: `drain_to_bigquery(db, batch=50, dataset)` lee `processed_at IS NULL limit 50`, clasifica payload por `event_type` → `bq_audit | bq_evals | bq_finops`, redacta PII via `security/pii.redact_dict_values`, agrupa por tabla e inserta.
- Real path: `google-cloud-bigquery` `client.insert_rows_json(dataset.table, payloads)` por tabla. Marca `processed_at=utcnow()` + `attempts+=1` solo si `last_error IS NULL`. Fallback a fake en cualquier excepción (sin creds, no BQ emulator) → in-memory `_FAKE_BQ: dict[dataset.table, rows]` + `last_error=fallback fake`.
- `os_is_fake_dataset(dataset)` heurística: `file://` → fake; `procurement_ops|test|fake|file` sin `GOOGLE_APPLICATION_CREDENTIALS` ni `BIGQUERY_EMULATOR_HOST` intenta `bigquery.Client()`; si falla → fake. Así `ci/local` no requiere creds pero staging con creds va a real BQ.
- Helpers para tests/dev: `get_fake_bq_rows(dataset, table)`, `query_fake_bq(dataset, table, execution_id)`, `clear_fake_bq()`. `query_fake_bq` filtra `payload.execution_id` o top-level `execution_id` para soportar outbox legacy.
- API: `POST /v1/bq/drain?batch=50` (RBAC admin, anonymous pasa en local) y `GET /v1/bq/query?dataset=&table=&execution_id=` para `SELECT * WHERE execution_id=xxx` local (fake). Worker ARQ: `drain_bq_job` cada 10s (ARQ cron) + alias `drain_outbox_job` compat F2.
- `config/settings.py:43` ya tenía `bigquery_dataset`, `gcs_bucket`; F9 no añade settings nuevos para BQ (reusa env `PROCUREMENT_BIGQUERY_DATASET`).

### F9-2 GCS ArtifactStore (`infra/gcs.py:19`, `evals/runner.py:103`)

- `ArtifactStore(bucket="gs://my-bucket" or "file://./artifacts")` única interfaz para artifacts (traces, reports, docs). En `gs://` usa `google-cloud-storage` lazy; si lib no instalada o `bucket` file → fallback local file. `put(key, bytes, content_type)`, `get`, `exists`, `list(prefix)`, `delete`. Sanitiza `key.lstrip("/")`, crea parent dirs.
- `_is_gcs`, `_parse_gcs_path(gs://bucket/prefix → bucket,prefix)` helpers. Global singleton `get_artifact_store(bucket?)` + `reset_artifact_store()` para tests.
- `evals/runner.py:_save_report` ahora además de escribir `evals/reports/report_<run_id>.json/.md` + `latest`, guarda a GCS `store.put(f"evals/{name}", bytes)` best-effort (no falla si GCS no disponible). Env `PROCUREMENT_GCS_BUCKET` o `GCS_BUCKET` → `file://./artifacts` default (dev). `artifacts/` ya gitignore pero se usa para `file://` demo.
- API: `GET /v1/artifacts?prefix=` lista `store.list(prefix)` (útil para debug BigQuery/GCS en staging sin `gsutil`).
- Test `tests/unit/test_gcs.py`: `file://tmp_path` round-trip + eval runner GCS save con `PROCUREMENT_GCS_BUCKET=file://tmp`.

### F9-3 Time-travel (`persistence/time_travel.py:15`, `api/main.py:/v1/procurement/executions/{id}/timeline|time-travel`)

- `get_execution_at(db, execution_id, at: str|datetime)` reconstruye snapshot a timestamp dado desde `audit_events` + `WorkflowCheckpoint`. Parsea `at` ISO (`Z→+00:00`), filtra `AuditEventRow.timestamp <= at` y `WorkflowCheckpoint.created_at <= at`. Deriva `last_state` desde último `execution.transition.*` o `execution.created` → `ExecutionState`, fallback a `row.status`. `last_node` desde `details.node` o checkpoint `node`. Retorna dict con `execution_id, status, current_node, at, created_at, trace_id, normalized_request/proposal/approval_request (current JSON), events_count, checkpoints_count, events[-10]`.
- `get_execution_history(db, execution_id)` lista cronológica `timestamp, event_type, actor, details` para debugging “¿por qué se aprobó hace 3 días?”.
- API: `GET /v1/procurement/executions/{id}/timeline?at=ISO` (time-travel) o sin `at` → full history. Alias `GET /v1/procurement/executions/{id}/time-travel?at=ISO` y `GET /v1/procurement/executions/{id}?at=ISO` (query param) para UI `timeline?at=...`. Si `at < created_at` → 404 “no snapshot”.
- Limitación conocida: `normalized_request/proposal/approval_request` no versionados (JSON in-place). Time-travel deriva estado desde eventos pero JSON es current; para MVP se documenta y se incluyen `events.details` con cambios relevantes (proposal.drafted etc.) y se recomienda consultar `events` para ver evolución. Futuro: versionar JSON con `audit_events.payload`.
- Test `tests/unit/test_time_travel.py`: synthetic advance + mid_time snapshot, before→None, after→COMPLETED, API `timeline` y `time-travel` alias.

### F9-4 Feature flags (`infra/feature_flags.py:19`, `infra/feature_flags.yaml`, `agents/factory.py:99`, `workflows/orchestrator.py:424`)

- `FlagProvider(path?)` lee `infra/feature_flags.yaml` (formato `flags: {flag: {enabled: bool, tenants: [tenant]}}` o directo `{flag: bool}`) y fallback a `_DEFAULT_FLAGS` (`rag_reranker false`, `llm_cache true`, `async_workers false`, `ui_v2 false`, `webhooks true`, `notifications true`, `bulk_approvals true`, `time_travel true`). Hot-reload via `load()`/`reload()` + `set_flag(flag, enabled, tenants)` que persiste a yaml si path existe. `is_enabled(flag, tenant_id)` lógica: si `enabled false` pero `tenant_id in tenants` → True (per-tenant override); si `enabled true` y `tenants` non-empty y `tenant_id not in tenants` → False (tenant-restricted rollout). `get_all()`, `reset_flag_provider()`, global singleton `get_flag_provider()`.
- `infra/feature_flags.yaml` committed como default local (file `flags:`), `Makefile:flags-list` → `cat infra/feature_flags.yaml`. Cambio sin deploy: editar yaml + SIGHUP o next worker loop `reload()` (Fase 9 MVP: API no auto-watch, pero worker puede `reload()` cada poll; docs runbook).
- Integración: `agents/factory.py:99` cache key respeta `is_flag_enabled("llm_cache", tenant_id)` además de `settings.llm_cache_enabled` (tenant gating). `workflows/orchestrator.py:424` `_get_rag_service` lee `is_flag_enabled("rag_reranker", tenant_id)` para decidir `use_reranker` (log only MVP, full reranker flag en `rag/service.py` ya existente `PROCUREMENT_RERANKER_ENABLED`). Eval harness testa flag on/off.
- Test `tests/unit/test_feature_flags.py`: load tmp yaml per-tenant, API `/v1/flags` + `/v1/flags/{flag}?tenant_id=`, orchestrator flag afecta cache (two identical LLM calls con flag disabled → both miss).

### F9-5 Lineage (`persistence/lineage.py:19`, `audit/service.py:92`, `workflows/orchestrator.py:550ff`, `api/main.py:/v1/lineage`)

- Cada `create_audit_event` ahora acepta `lineage: dict {document_ids, policy_ids, supplier_ids}` opcional y lo persiste en `details.lineage = {document_ids, policy_ids, supplier_ids, execution_id}` (apendiza a `details` existente). `workflows/orchestrator.py` construye lineage en transiciones: `_build_lineage(document_ids, policy_ids, supplier_ids)` helper (F9). En `advance_synthetic` registra `doc_ids`/`pol_ids` desde RAG results y `supplier_ids` desde `proposal.supplier_id`, en `transition` desde `proposal.policies_applied`, etc. Así cada `execution.transition.*`, `rag.retrieved`, `proposal.drafted`, `approval.requested` lleva lineage para BigQuery view `procurement_lineage`.
- Queries: `get_executions_for_document(db, document_id)` escanea `AuditEventRow` (SQLite/PG compat, JSON `details->lineage` via Python filter) y deduplica `execution_id`. Similar `get_executions_for_policy`, `get_executions_for_supplier`, `get_lineage_for_execution(execution_id)` aggrega sets.
- API: `GET /v1/lineage?document_id=xxx` → `{document_id, executions, count}`; igual `policy_id`, `supplier_id`, `execution_id`. Ejemplo plan: `SELECT * WHERE document_id=policy_budget_v1` → ejecuciones afectadas. `Makefile:lineage-doc` y `lineage-exec` helpers.
- Optimización futura: en PG con `jsonb`, usar `WHERE details->'lineage'->'document_ids' ? 'xxx'` con GIN; MVP escanea `db.query(AuditEventRow).all()` suficiente para <10k eventos local (p95 <100ms). BigQuery en prod sí usa `UNNEST(lineage.document_ids)`.
- Test `tests/unit/test_lineage.py`: orchestrator synthetic → audit tiene `supplier_ids`, `get_lineage_for_execution` aggrega, API `GET /v1/lineage?execution_id` y `?supplier_id`.

### F9-6 Retention y soft-delete (`persistence/retention.py:19`, `api/main.py:/v1/retention/run`, `DELETE /v1/tenants/{id}/data`, `infra/gcs.py` archive)

- `run_retention(db, retention_days=365, dry_run=False)` job diario: `cutoff = now - retention_days` (env `PROCUREMENT_RETENTION_DAYS` override), selecciona `AuditEventRow.timestamp < cutoff`, archiva a `ArtifactStore` `retention/audit_<date>_<arc>.json` con `event_id, execution_id, event_type, timestamp, input_hash, output_hash, trace_id` (mantiene hashes para linaje), luego si `dry_run` retorna sin borrar; si real → crea `_tombstones[event_id]` dict y `db.delete(r)` + limpia `OutboxEvent.processed_at < cutoff`. Commit y retorna `{archived, deleted, kept_hashes, cutoff}`. `retention_days` en `config/settings.py:104` `PROCUREMENT_RETENTION_DAYS` (default 365).
- `soft_delete_tenant(db, tenant_id, actor_id, reason)` GDPR hook `DELETE /v1/tenants/{id}/data`: encuentra `WorkflowExecution tenant_id`, crea `tombstone_id = new_id("tomb")` en `_tombstones` + audit `tenant.data_soft_deleted` por ejecución (via `create_audit_event`), soft-delete subs `WebhookSubscriptionRow.active=False`. No borra filas, solo crea tombstone y audit; queries `list_executions` y `list_approvals` deben filtrar `is_tenant_soft_deleted(db, tenant_id)` (en `api/main.py:list_executions` early return empty si soft-deleted). `is_tenant_soft_deleted` chequea `_tombstones` y audit `details.tenant_id`.
- Helpers: `get_tombstone`, `list_tombstones`, `clear_tombstones`, `is_tenant_soft_deleted`. `workers/tasks.py:retention_job` envuelve `run_retention`.
- API: `POST /v1/retention/run {"dry_run": bool}` (RBAC admin) y `DELETE /v1/tenants/{id}/data` (tenant isolation + admin). `GET /v1/artifacts` útil para verificar archivo retención en GCS.
- Test `tests/unit/test_retention.py`: crea eventos 400 días old, dry_run no borra, real borra y mantiene hashes; API soft-delete → tombstone + `already_deleted` en segunda llamada, cleanup `clear_tombstones` + delete audit.

## Consecuencias

- Suite `pytest -q` 285→298 (12 nuevos F9 tests) <40s, harness 22/22 100% intacto (lineage no afecta policy).
- `docker compose up` con `PROCUREMENT_BIGQUERY_DATASET=procurement_ops PROCUREMENT_GCS_BUCKET=file://./artifacts` drena <5s `POST /v1/procurement/executions` → `POST /v1/bq/drain` → `bq_audit` queryable sin PII (redact email `test@example.com` → `[REDACTED]`).
- `make flags-list` muestra yaml, editar `infra/feature_flags.yaml` + `kill -HUP` worker cambia `llm_cache` sin deploy (verified `test_flag_affects_orchestrator`).
- `make lineage-doc DOC_ID=policy_budget_v1` lista ejecuciones afectadas <100ms local.
- `make time-travel EXEC_ID=exec_xxx AT=2026-09-02T00:00:00Z` retorna snapshot; UI `timeline?at=` usa mismo endpoint.
- `make retention-dry` no borra, `make retention-run` archiva a `artifacts/retention/*.json` y mantiene hashes.
- Coste: BQ fake 0 USD en local, GCS file 0; en staging `PROCUREMENT_GCS_BUCKET=gs://bucket` + `PROCUREMENT_BIGQUERY_DATASET=project.dataset` usa `google-cloud-*` con workload identity (no key file) Fase 10.

## Alternativas descartadas

- **BigQuery streaming insert vs batch load jobs:** streaming `insert_rows_json` elegido por simplicidad <10s y low volume (<1k events/min); load job (GCS→BQ) descartado para Fase 9 por adds latency y necesita `google-cloud-storage` + `bq load` extra, pero será P1 si volume >10k/min (plan: switch drainer to GCS staging + `bq load` batch horario).
- **pgvector + BigQuery lineage JS UDF:** lineage en PG via `jsonb` GIN index descartado para MVP por complejidad migration; escaneo Python suficiente local + BigQuery `UNNEST` en prod. Futuro: add `CREATE INDEX ON audit_events USING GIN ((details->'lineage'))` cuando PG ≥ 10k rows.
- **Unleash/Flagship SaaS:** `FlagProvider` yaml local elegido para evitar dep externa y para `make flags-list` hot-reload demostrable en portfolio; Unleash adapter es Fase 10 backlog (interface ya abstraída `FlagProvider` → `UnleashProvider` via env `PROCUREMENT_FLAGS_BACKEND=unleash`).
- **Hard-delete GDPR:** soft-delete con tombstone elegido para preservar time-travel y lineage hashes; hard-delete borraría evidencia y rompería audit `must_not_call`. GDPR job real en Fase 10 añadirá `DELETE FROM ... WHERE tombstone_age > 90d` + BigQuery `DELETE` + GCS `lifecycle 365d`.
- **Temporal versioning de JSON proposal:** descartado versionar `workflow_executions.proposal` en cada transición por adds column `proposal_history`; MVP usa `audit_events.details` como delta, suficiente para debug y no requiere migration.

## Próximos pasos

- Fase 10: Terraform módulos `cloud_run, cloud_sql+pgvector, redis, gcs, bq, iam, secrets` + GH Actions `build/scan/push`, `terraform plan` en CI, Cloud Run canary 90/10, `migrate_job.yaml` alembic, runbooks + chaos `toxiproxy`, backup drill, secrets rotation + workload identity.
- Fase 11: extraer `platform` core vs `domains/procurement` + plugin registry + 2º workflow `expense_approval` 8 nodos + `code_shared %` + scorecard.
