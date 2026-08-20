# Enterprise Agentic AI Platform — Procurement (Human-in-the-Loop)

Plataforma independiente de **Agent Station** para ejecutar workflows multi-etapa con estado durable, tool gateway, RAG seguro y aprobación humana.

> **Boundary:** Agent Station es sistema externo. Comunicación exclusivamente por APIs versionadas (`/v1`) y eventos. Ver `docs/architecture/boundary-agent-station.md`.

## Estado actual — Fase 0, 1, 2, 3, 4, 5, 6 y 7 completadas (2026-08-20)

| Fase | Objetivo | Estado | Criterio de salida verificado |
|------|----------|--------|-------------------------------|
| 0 — Reconocimiento y baseline | Contrato externo, fake, decisiones | ✅ | `docker compose up` levanta fake; docs explican boundary |
| 1 — Esqueleto ejecutable | Servicio FastAPI, contratos, persistencia, ejecución sintética | ✅ | `POST /v1/procurement/executions` → `AWAITING_APPROVAL` → `COMPLETED` tras aprobación |
| 2 — Dominio determinista | Inventario, faltantes, proveedores, policy checks sin LLM | ✅ | Mismos fixtures → mismo `qty`/`total`; 51 tests; cálculos críticos no llaman al modelo |
| 3 — RAG seguro | Pipeline GCS→chunks→pgvector, filtros, citas, bloqueo malicioso | ✅ | Recupera evidencia relevante con trazabilidad, bloquea `malicious_document`, no ejecuta con texto no confiable; 79 tests |
| 4 — Runtime agente y grafo | Gemini → DeepSeek fallback, tool gateway, 14 nodos, validación | ✅ | Flujo feliz produce propuesta válida con `total` recalculado; salida inválida del LLM se corrige/bloquea sin efecto externo; 99 tests |
| 5 — Human approval y ejecución | Snapshot inmutable, scope_hash, expiración, doble aprobación, reanudación durable, idempotencia completa | ✅ | Nunca ejecuta sin aprobación vigente; retry/reanudación no duplica orden; `scope_mismatch`/`expired` bloquean con 409; 118 tests |
| 6 — Evaluation layer v1 | Harness aislado, 14 casos, métricas, baseline, gate CI | ✅ | Cambio de prompt/modelo/grafo produce diff medible; `success 100%`, `unsafe 0`, `duplicate 0`; gate CI bloquea regresión; 125 tests |
| 7 — Seguridad adversarial | Threat model, PII, injection, tenant isolation, budgets, rate limits | ✅ | 22/22 100% `unsafe 0 duplicate 0` con 8 adversariales; `pip-audit` + gate; 162 tests |

Siguientes fases: ver `PLAN_IMPLEMENTACION.md` §19 y §27.

## Quickstart local (sin GCP)

```bash
# 1. Instalar
pip install -e ".[dev]"

# 2. Levantar Postgres + Redis + API + fake Agent Station
docker compose up --build -d

# 3. Ver salud
curl http://localhost:8000/healthz
curl http://localhost:8000/readyz

# 4. Crear ejecución sintética
curl -X POST http://localhost:8000/v1/procurement/executions \
  -H "Content-Type: application/json" \
  -d '{"tenant_id":"tenant_demo","requester_id":"user_01","raw_intent":"Necesitamos reponer materiales críticos para las próximas tres semanas."}'

# 5. Aprobar (usa approval_id de la respuesta anterior)
curl -X POST http://localhost:8000/v1/approvals/{approval_id}/decision \
  -H "Content-Type: application/json" \
  -d '{"decision":"approved","decided_by":"approver_01"}'

# 6. Evaluar
python -m procurement_platform.evals.runner --base-url http://localhost:8000
```

Sin Docker (SQLite + tests):

```bash
pytest -q
python -m procurement_platform.evals.runner  # requiere API corriendo
```

## Estructura

```
src/procurement_platform/
  api/            FastAPI (healthz, readyz, executions, approvals/{id}, approvals/{id}/decision, executions/{id}/resume, documents, rag/search) + rate limit/payload guard (Fase 7)
  domain/         Contratos + inventory, suppliers (ApprovalRequest con snapshot/scope_hash/required_approvals)
  policies/       Policy engine determinista
  rag/            RAG seguro: models, FakeEmbedder 384, security, ingestion (PII redact), retrieval, service
  agents/         LLM: adapter, Gemini, DeepSeek (fallback), Fake, prompts versionados, factory
  tools/          Gateway: definitions, allowlist, budgets, tenant/rate, idempotencia global + locks (Fase 7)
  approvals/      Service Fase 5: snapshot, scope_hash, expiración, doble aprobación, locks
  security/       PII detect/redact, input_validation (injection), tenant isolation, rate_limiter (Fase 7)
  evals/          Harness v2 Fase 7: harness.py (22 casos), runner.py (--mode direct --gate), reports/baseline_v2.json
  workflows/      Orchestrator Fase 7 (injection/PII checks + LLM sanitize) + graph 14 nodos
  integrations/agent_station/  Cliente aislado + DTOs externos + fake
  persistence/    SQLAlchemy + Alembic (workflow_executions, inventory_*, suppliers, purchase_*, documents, document_chunks) + pgvector
  config/         Settings tipadas (llm_provider, budgets, prompt/graph, rate/payload)
  evals/          Fixtures + casos Fase 3-7 (22 casos: 14 + 8 adversariales)
docs/
  architecture/boundary-agent-station.md, overview.md (Fase 0-7)
  decisions/0001-0009 (boundary, orchestrator, stack, fase2, fase3 RAG, fase4 agente, fase5 approval, fase6 eval, fase7 seguridad)
  security/threat-model.md (Fase 7)
evals/procurement/happy_path.json, malicious_document.json, conflicting_policy.json + 18 nuevos (incl. prompt_injection_direct, tenant_isolation, pii_exfiltration, approval_replay...)
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
- Fase 8-11: Observabilidad, GCP staging, hardening

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
