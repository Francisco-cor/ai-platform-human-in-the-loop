# Enterprise Agentic AI Platform — Procurement (Human-in-the-Loop)

Plataforma independiente de **Agent Station** para ejecutar workflows multi-etapa con estado durable, tool gateway, RAG seguro y aprobación humana.

> **Boundary:** Agent Station es sistema externo. Comunicación exclusivamente por APIs versionadas (`/v1`) y eventos. Ver `docs/architecture/boundary-agent-station.md`.

## Estado actual — Fase 0–11 completadas (2026-09-02) — v1.0.0 OSS

![scorecard](reports/scorecard.md) **code_shared 79%** | **329 tests** | **22+1 casos eval 100%** | **Spectral 0** | **P99 <1s**

| Fase | Objetivo | Estado | Criterio de salida verificado |
|------|----------|--------|-------------------------------|
| 0 — Reconocimiento y baseline | Contrato externo, fake, decisiones | ✅ | `docker compose up` levanta fake; docs explican boundary |
| 1 — Esqueleto ejecutable | Servicio FastAPI, contratos, persistencia, ejecución sintética | ✅ | `POST /v1/procurement/executions` → `AWAITING_APPROVAL` → `COMPLETED` tras aprobación |
| 2 — Dominio determinista | Inventario, faltantes, proveedores, policy checks sin LLM | ✅ | Mismos fixtures → mismo `qty`/`total`; 51 tests; cálculos críticos no llaman al modelo |
| 3 — RAG seguro | Pipeline GCS→chunks→pgvector, filtros, citas, bloqueo malicioso | ✅ | Recupera evidencia relevante con trazabilidad, bloquea `malicious_document`, no ejecuta con texto no confiable; 79 tests |
| 4 — Runtime agente y grafo | Gemini → DeepSeek fallback, tool gateway, 14 nodos, validación | ✅ | Flujo feliz produce propuesta válida con `total` recalculado; salida inválida del LLM se corrige/bloquea sin efecto externo; 99 tests |
| 5 — Human approval y ejecución | Snapshot inmutable, scope_hash, expiración, doble aprobación, reanudación durable, idempotencia completa | ✅ | Nunca ejecuta sin aprobación vigente; retry/reanudación no duplica orden; `scope_mismatch`/`expired` bloquean con 409; 118 tests |
| 6 — LLMOps, prompt registry y governance | Prompts versionados, cache, A/B, budgets | ✅ | `prompts/registry` hash `sha256`, `llm_matrix` 3 providers, cache hit >30%, `prompt A/B` gate 5%, `prompt_lint` + per-tenant budgets; 262 tests; `prompt_hash` en audit |
| 7 — HITL productivo | Inbox UI + notificaciones + SLA + bulk | ✅ | Inbox <2min, notificación <60s, escalamiento 12h, ScopeDiff, timeline, bulk 3× + CSV; 275 tests |
| 8 — API Platform, DX e integraciones | SDK py/ts, webhooks, paginación, OpenAPI lint | ✅ | `pip install procurement-sdk-py` crea/aprueba sin curl, webhook `execution.completed` HMAC <5s, `openapi.json` Spectral 0, paginación `total_count/has_more` estable, `docs/api/postman_collection.json`; 285+ tests |
| 9 — Data platform y analytics | Outbox→BQ, lineage, time-travel, feature flags, retention | ✅ | `POST /v1/bq/drain` → `bq_audit` en <5s sin PII, `GET /v1/lineage?document_id=...` lineage, `GET .../time-travel?at=...` snapshot, `flags.yaml` hot-reload, `DELETE /v1/tenants/{id}/data` tombstone; 298+ tests |
| 10 — Cloud native, GitOps y SRE | Terraform modules, Cloud Run canary, migrate job, backups, chaos | ✅ | `terraform validate` staging+prod, `gcloud run deploy --traffic 90:stable,10:canary` + `curl /readyz`, `alembic upgrade head` Job + pgbouncer, `backup.sh restore-drill` + 7 runbooks SLO 99.9% p95<1s; 310+ tests |
| 11 — Ecosistema extensible y OSS 1.0 | Platform core, plugin registry, expense workflow, scorecard, release | ✅ | `POST /v1/expense/executions {amount:1200}` → `AWAITING→COMPLETED` reusa 79% `platform`, `scripts/scorecard.py` PASS, `CONTRIBUTING.md` <10 min; 329+ tests |

> **Why not a chatbot?** Esta plataforma demuestra ingeniería de agentes: workflows multi-etapa con estado durable, tool gateway con allowlist/budgets/idempotencia, RAG seguro con quarantine, human-in-the-loop con snapshot/scope_hash, evaluación offline 22 casos, audit trail con trace_id, BigQuery lineage, GitOps canary y 2º dominio en <1 día con >70% código compartido — no es solo prompt→respuesta.

Siguientes iteraciones: ver `CHANGELOG.md` `## [1.0.0]` y `PLAN_ELEVACION_11_FASES.md` §4.

## Try in 5 min (sin GCP) — Fase 11

```bash
git clone <repo> && cd ai-platform-human-in-the-loop
pip install -e ".[dev]" && pip install -e sdk/python
docker compose up --build -d && curl http://localhost:8000/healthz
pytest -q  # 329 tests
python examples/sdk_happy.py  # procurement happy
curl -X POST http://localhost:8000/v1/expense/executions -H "Content-Type: application/json" -d '{"tenant_id":"tenant_demo","requester_id":"user_01","amount":1200,"currency":"USD","reason":"viaje"}'
# → {"status":"AWAITING_APPROVAL","approval_request":{"required_approvals":2}}; aprobar 2 veces → COMPLETED reusa platform
python scripts/scorecard.py  # 79% shared PASS
```

## Quickstart local (sin GCP) — detallado

```bash
# 1. Instalar
pip install -e ".[dev]"
pip install -e sdk/python  # SDK Python

# 2. Levantar Postgres + Redis + API + UI + fake Agent Station + Grafana
docker compose up --build -d

# 3. Ver salud + métricas + OpenAPI
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz
curl http://localhost:8000/metrics | head
python tools/openapi_lint.py --check  # Spectral 0

# 4. Crear ejecución vía SDK (sin curl)
python examples/sdk_happy.py
# o curl
bash examples/curl_happy.sh

# 5. Aprobar vía SDK o inbox UI http://localhost:3001
curl -X POST http://localhost:8000/v1/approvals/{approval_id}/decision \
  -H "Content-Type: application/json" \
  -d '{"decision":"approved","decided_by":"approver_01"}'

# 6. Webhook: suscribir y recibir execution.completed
curl -X POST http://localhost:8000/v1/webhooks/subscriptions -H "Content-Type: application/json" \
  -d '{"tenant_id":"tenant_demo","url":"http://webhook.site/test","secret":"secret123","events":["execution.completed"]}'
# al completar, webhook recibe HMAC sha256 + X-Webhook-Id

# 7. Evaluar + RAG + matrix + cross-domain
python -m procurement_platform.evals.runner --mode direct --suite all  # 22/22
python -m procurement_platform.evals.rag_eval  # 50/50 precision 1.0
python -m procurement_platform.evals.llm_matrix --providers fake gemini deepseek
make eval-all-domains  # procurement 22/22 + expense 1/1
make scorecard-check  # 79% shared
```

Sin Docker (SQLite + tests + SDK mock):

```bash
pytest -q  # 310+ tests
pytest sdk/python/tests/test_client.py -v  # SDK
python -m procurement_platform.evals.runner --mode direct --suite all
make terraform-validate  # tflint + terraform validate staging/prod
make docker-build VERSION=0.1.0  # sbom via syft
make chaos-test  # toxiproxy DB failover sin duplicate
```

Postman: `docs/api/postman_collection.json` → Import en Postman, variable `baseUrl=http://localhost:8000`.
```

## Estructura

```
src/procurement_platform/
  api/            FastAPI Fase 10 (healthz/readyz/metrics/slo, executions CRUD + time-travel, approvals/bulk/export/delegation/sla, webhooks/subscriptions, documents, rag/search, lineage, bq/query, retention, secrets/rotate) + rate limit/payload guard + OpenAPI lint
  domain/         Contratos + inventory, suppliers (ApprovalRequest snapshot/scope_hash/required_approvals + escalated_to/delegated_from)
  policies/       Policy engine determinista
  rag/            RAG seguro: models, FakeEmbedder 384, security, ingestion, retrieval, service, reranker, feedback
  agents/         LLM: adapter, Gemini, DeepSeek, Fake, prompts registry (procurement-v1/v2 hash), factory (fallback), cache (tenant TTL 1h, flag-gated)
  tools/          Gateway: definitions, allowlist, budgets, tenant/rate, idempotencia + locks
  approvals/      Service Fase 7: snapshot, scope_hash, expiración, doble aprobación, SLA 12h, delegation, locks
  notifications/  Service Fase 7: Email/Slack/Webhook Notifier + inbox link
  integrations/webhooks/  Fase 8: WebhookService HMAC sha256, retry, X-Webhook-Id, outbox drainer + AgentStation callback
  integrations/agent_station/  Cliente aislado + DTOs + webhooks
  security/       PII, input_validation, tenant isolation, rate_limiter (per-tenant llm tokens), secrets_rotation (WIF)
  evals/          Harness Fase 10: harness 22 casos, runner (direct/api/gate, prompt A/B + GCS), llm_matrix, rag_eval
  workflows/      Orchestrator Fase 10 (injection/PII, LLM sanitize, lineage, flags) + graph 14 nodos
  persistence/    SQLAlchemy + Alembic + time_travel/lineage/retention + pgbouncer + Cloud SQL
  infra/         Locks, gcs ArtifactStore, feature_flags, terraform modules (cloud_run/cloud_sql/redis/gcs/bq/iam/secrets), deploy canary, db migrate_job, backup
  pipeline/       BQ drainer Fase 9 + SRE jobs (drain, retention)
  config/         Settings tipadas (llm_provider, budgets, prompt/graph, rate/payload, tenant_llm_config, gcs_bucket, bigquery_dataset, retention_days)
  observability/  OTel tracing, Prometheus metrics, Grafana dashboards (8 panels), alerts (SLO 99.9%)
sdk/python/      Fase 8: ProcurementClient + tests
sdk/ts/          Fase 8: ProcurementClient TS + tests
ui/              Fase 7: Next.js 14 inbox
docs/
  api/openapi.json (28 paths, Spectral 0), changelog.md, postman_collection.json
  architecture/boundary-agent-station.md, overview.md (Fase 0-10)
  decisions/0001-0014 (incl. LLMOps, HITL, API platform, Data platform, Cloud native)
  operations/SLO.md + runbooks/ (7) + restore_drill.md
infra/terraform/modules/{cloud_run,cloud_sql,redis,gcs,bq,iam,secrets}, envs/{staging,prod}
infra/deploy/cloud_run_deploy.sh (canary 90:stable,10:canary) + infra/db/migrate_job.yaml + infra/backup/backup.sh
observability/dashboards/procurement.json, alerts/alerts.yaml
infra/feature_flags.yaml + config/cost_rates.yaml
examples/        curl_happy.sh, sdk_happy.py, etc.
```
```

## Contratos principales

- `ExecutionState`: RECEIVED → NORMALIZED → ... → AWAITING_APPROVAL → APPROVED → ACTION_EXECUTED → VERIFIED → COMPLETED (+ BLOCKED/EXPIRED/REJECTED/NEEDS_CLARIFICATION) (ver `domain/models.py:30`)
- `POST /v1/procurement/executions` (Idempotency-Key, RateLimit 60/min, Payload 256KB), `GET /v1/procurement/executions/{id}`, `GET .../events`, `GET /v1/approvals/{id}` (snapshot + scope_hash), `POST /v1/approvals/{id}/decision` (approved/rejected/needs_changes con `Idempotency-Key`), `POST /v1/procurement/executions/{id}/resume` (reanudación durable), `POST /v1/documents` (ingesta RAG), `GET /v1/rag/search` (citas)
- `ApprovalRequest` Fase 5: `approval_id`, `proposal_id`, `scope_hash`, `proposal_snapshot` inmutable, `risk_level/total/currency`, `required_approvals` (1 o 2 si high), `approvers`, `expires_at` 24h, `is_expired()/is_scope_valid()`.
- `AuditEvent` append-only con `trace_id` y hashes + `rag.*` + `tool.*` + `proposal.drafted` + `approval.*` (`requested/partially_approved/decided/expired/scope_mismatch`) + `security.*` (`direct_injection_detected`, `pii_redacted`, `tool_hijacking_blocked`, `tenant_isolation_violation`, `budget_exceeded`) (Fase 7).
- `Eval Case` Fase 7: `case_id`, `description`, `input`, `fixtures`, `expected` (`terminal_state`, `must_not_call`, `required_events`, `policy_decisions`, `max_latency_s`), `tags`, `seed`; harness `evals/harness.py:30` `run_suite()` 22 casos + `evals/runner.py:40` `--mode direct --gate --baseline`.
- **Fase 2 — cálculo determinista:** `domain/inventory.py:114` `calculate_shortage_for_item()`, `domain/suppliers.py:48` `SupplierCatalog.search()`, `policies/engine.py:200` `run_policy_checks()`.
- **Fase 3 — RAG seguro:** `rag/security.py:20` `detect_prompt_injection()`, `rag/ingestion.py:71` `IngestionPipeline.ingest()` (PII redact Fase 7), `rag/retrieval.py:40` `RetrievalService.retrieve()`, `rag/service.py:40` `RagService.retrieve_for_execution()`.
- **Fase 4 — agente y gateway:** `agents/adapter.py:20` `LLMRequest/LLMResponse`, `agents/gemini.py:20` `GeminiAdapter`, `agents/deepseek.py:20` `DeepSeekAdapter` (fallback), `agents/factory.py:40` `LLMFactory.generate_with_fallback()`, `tools/gateway.py:80` `ToolGateway.call()` (allowlist, budgets, tenant/rate, idempotencia global + locks), `workflows/graph.py:30` 14 nodos con `duration_ms`/`tokens` y recálculo determinista.
- **Fase 5 — aprobación y ejecución:** `approvals/service.py:10` `create_approval_request()` + `compute_required_approvals()` + `validate_scope_or_raise()`, `workflows/orchestrator.py:300` `approve_and_complete()` (lock, expiración, scope, doble aprobación, `submit_purchase_order` idempotente, verificación, `resume_durable()`), `tools/gateway.py:62` store global + locks.
- **Fase 6 — evaluación:** `evals/harness.py:30` `run_case_direct()` / `run_suite()` (14→22 casos, `clear_db`, captura `events/tool_calls/tokens/cost/latency`), `compute_suite_metrics()` (`task_success_rate`, `tool_call_accuracy`, `latency p50/p95`, `unsafe/duplicate`), `evals/runner.py:40` CLI + `_gate_check()` + `_save_report()` + `baseline_v1.json`/`baseline_v2.json`.
- **Fase 7 — seguridad:** `security/pii.py:20` `detect_pii()`/`redact_pii()`, `security/input_validation.py:10` `validate_raw_intent()`, `security/tenant.py:10` `is_tenant_allowed()`, `security/rate_limiter.py:20` `RateLimiter.check_and_hit()`, `workflows/orchestrator.py:440` injection/PII checks + `security.direct_injection_detected`, `observability/logging.py:20` PII/secret redaction.

## Boundary Agent Station

- Cliente: `src/procurement_platform/integrations/agent_station/client.py`
- DTOs externos: `dtos.py`
- Fake: `fake.py` + `fake_server.py` (puerto 8001)
- Docs: `docs/architecture/boundary-agent-station.md`

## Roadmap

- Fase 2: Dominio determinista — ✅ (51 tests)
- Fase 3: RAG seguro — ✅ (79 tests)
- Fase 4: Runtime agente y grafo — ✅ (99 tests: LLM Gemini→DeepSeek→fake, gateway, graph 14 nodos, recálculo determinista, budgets)
- Fase 5: Human approval + idempotencia — ✅ (118 tests: snapshot, scope_hash, expiración, doble aprobación, gateway idempotente, resume durable)
- Fase 6: Evaluation layer v1 — ✅ (125 tests: harness 14 casos, `task_success 100%`, `unsafe 0`, `duplicate 0`, baseline, gate CI)
- Fase 7: Seguridad adversarial — ✅ (162 tests: 22 casos, `task_success 100%`, `unsafe 0`, `duplicate 0`, threat model, PII, injection, tenant isolation, budgets, rate limits, pip-audit)
- Fase 8: API Platform — ✅ (285 tests: SDK py/ts, webhooks HMAC, paginación estable, OpenAPI Spectral 0)
- Fase 9: Data Platform — ✅ (298 tests: outbox→BQ drainer, GCS ArtifactStore, time-travel, lineage, feature_flags, retention tombstone)
- Fase 10: Cloud native — ✅ (310+ tests: terraform 7 módulos + envs staging/prod, cd.yml build/scan/push/sbom, Cloud Run canary 90:stable,10:canary, migrate Job + pgbouncer, SLO 99.9% p95<1s, 7 runbooks, backup drill)
- Fase 11: Ecosistema extensible — pendiente (platform core, plugin registry, expense workflow, scorecard)

**Ejemplo Fase 7 — evaluación y seguridad:**
- `make eval` (`--mode direct --suite all`) → `22/22` `100%` `p50 0.07s p95 0.09s` `cost $0.0008` `unsafe 0` `duplicate 0`; `evals/reports/report_<run_id>.json/.md` + `latest` + `baseline_v2.json` con `prompt_version`, `graph_version`, `code_commit`.
- `make eval-gate` (`--gate --baseline baseline_v2.json`) falla si `unsafe>0`, `duplicate>0` o `success` cae >10% vs baseline; cambio de `prompt_version`/`graph_version` produce diff medible.
- `make eval-security` (`pytest tests/security -v`) valida 0 unsafe en suite adversarial.
- `pytest tests/integration/test_eval_regression.py` valida harness, métricas y gate.

**Ejemplo Fase 6 — evaluación y gate:**
- `make eval` (`--mode direct --suite all`) Fase 6: `14/14` `100%`; Fase 7: `22/22` `100%`.
- `pytest tests/integration/test_security_adversarial.py` valida PII redaction, injection block, tenant isolation, replay, rate limit, payload limit.

**Ejemplo Fase 5 — aprobación + idempotencia:**
- `POST /v1/procurement/executions` → `AWAITING_APPROVAL` con `proposal_snapshot` y `scope_hash`; `GET /v1/approvals/{id}` muestra desglose + `required_approvals`.
- `POST .../decision {approved, approver_01}` con `risk high` → `partially_approved` (requiere 2), segundo `approver_02` → `COMPLETED` con `order_exec_xxxx` idempotente; si `scope_hash` cambia tras aprobación → `409 scope_mismatch`; si expira → `409 expired` + `EXPIRED`; `Idempotency-Key` repetido → mismo resultado sin duplicar; `POST .../resume` tras reinicio → idempotente.
- `GEMINI_API_KEY` no configurada → `DeepSeek` fallback (Fase 4); `total` siempre recalculado determinísticamente; `search_suppliers` con `max 5` → `BLOCKED` si excede.

**Ejemplo Fase 4 — LLM + gateway:**
- `GEMINI_API_KEY` no configurada → `DeepSeek` fallback; sin ambos → `FakeAdapter` determinista (CI). `POST /v1/procurement/executions` con `raw_intent` ambiguo → `normalize_request` LLM propone `items`, sistema valida; `draft_proposal` LLM propone `supplier_demo` con `confidence 0.95`, sistema **recalcula** `total = 138*10.00=1380` e ignora `total` del LLM, registra `was_fallback` y `tokens`; si LLM devuelve `invalid_json` → validación falla, usa fallback determinista sin efecto externo; si `search_suppliers` excede `max 5` → `BLOCKED` con `tool.budget_exceeded`.

**Config LLM:**
```bash
PROCUREMENT_LLM_PROVIDER=auto  # auto|gemini|deepseek|fake
GEMINI_API_KEY=... GEMINI_MODEL=gemini-2.0-flash
DEEPSEEK_API_KEY=... DEEPSEEK_MODEL=deepseek-chat DEEPSEEK_BASE_URL=https://api.deepseek.com
PROCUREMENT_LLM_FALLBACK_ENABLED=true
```

## Criterio final de éxito

Ver `PLAN_IMPLEMENTACION.md` §28 (12 puntos reproducibles).

## Operación

```bash
make test
make lint
make run
make docker-up
make eval
```

Logs JSON estructurados con `request_id`, `trace_id`, `execution_id`.

## Licencia

AGPL-3.0-or-later
