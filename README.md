# Enterprise Agentic AI Platform — Procurement (Human-in-the-Loop)

Plataforma independiente de **Agent Station** para ejecutar workflows multi-etapa con estado durable, tool gateway, RAG seguro y aprobación humana.

> **Boundary:** Agent Station es sistema externo. Comunicación exclusivamente por APIs versionadas (`/v1`) y eventos. Ver `docs/architecture/boundary-agent-station.md`.

## Estado actual — Fase 0, 1, 2, 3 y 4 completadas (2026-08-20)

| Fase | Objetivo | Estado | Criterio de salida verificado |
|------|----------|--------|-------------------------------|
| 0 — Reconocimiento y baseline | Contrato externo, fake, decisiones | ✅ | `docker compose up` levanta fake; docs explican boundary |
| 1 — Esqueleto ejecutable | Servicio FastAPI, contratos, persistencia, ejecución sintética | ✅ | `POST /v1/procurement/executions` → `AWAITING_APPROVAL` → `COMPLETED` tras aprobación |
| 2 — Dominio determinista | Inventario, faltantes, proveedores, policy checks sin LLM | ✅ | Mismos fixtures → mismo `qty`/`total`; 51 tests; cálculos críticos no llaman al modelo |
| 3 — RAG seguro | Pipeline GCS→chunks→pgvector, filtros, citas, bloqueo malicioso | ✅ | Recupera evidencia relevante con trazabilidad, bloquea `malicious_document`, no ejecuta con texto no confiable; 79 tests |
| 4 — Runtime agente y grafo | Gemini → DeepSeek fallback, tool gateway, 14 nodos, validación | ✅ | Flujo feliz produce propuesta válida con `total` recalculado; salida inválida del LLM se corrige/bloquea sin efecto externo; 99 tests (LLM, gateway, graph, determinismo, budgets) |

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
  api/            FastAPI (healthz, readyz, executions, approvals, documents, rag/search)
  domain/         Contratos + inventory, suppliers
  policies/       Policy engine determinista
  rag/            RAG seguro: models, FakeEmbedder 384, security, ingestion, retrieval, service
  agents/         LLM: adapter, Gemini, DeepSeek (fallback), Fake, prompts versionados, factory
  tools/          Gateway: definitions, allowlist por estado, budgets, validación, idempotencia
  workflows/      Orchestrator Fase 4 (RAG + LLM draft recalculado) + graph (14 nodos: intake→normalize→load→retrieve→validate→shortage→suppliers→draft→policy→route→wait→execute→verify→summarize)
  integrations/agent_station/  Cliente aislado + DTOs externos + fake
  persistence/    SQLAlchemy + Alembic (inventory_*, suppliers, purchase_*, documents, document_chunks) + pgvector
  config/         Settings tipadas (llm_provider, budgets, prompt/graph versions)
  evals/          Harness + fixtures + casos Fase 3-4
docs/
  architecture/boundary-agent-station.md, overview.md (Fase 0-4)
  decisions/0001-0006 (boundary, orchestrator, stack, fase2, fase3 RAG, fase4 agente)
evals/procurement/happy_path.json, malicious_document.json, conflicting_policy.json, outdated_price.json
```

## Contratos principales

- `ExecutionState`: RECEIVED → NORMALIZED → ... → AWAITING_APPROVAL → APPROVED → ACTION_EXECUTED → VERIFIED → COMPLETED (ver `domain/models.py:30`)
- `POST /v1/procurement/executions` (Idempotency-Key), `GET /v1/procurement/executions/{id}`, `GET .../events`, `POST /v1/approvals/{id}/decision`, `POST /v1/documents` (ingesta RAG), `GET /v1/rag/search` (citas)
- `AuditEvent` append-only con `trace_id` y hashes + `rag.*` + `tool.*` + `proposal.drafted`.
- **Fase 2 — cálculo determinista:** `domain/inventory.py:114` `calculate_shortage_for_item()`, `domain/suppliers.py:48` `SupplierCatalog.search()`, `policies/engine.py:200` `run_policy_checks()`.
- **Fase 3 — RAG seguro:** `rag/security.py:20` `detect_prompt_injection()`, `rag/ingestion.py:71` `IngestionPipeline.ingest()`, `rag/retrieval.py:40` `RetrievalService.retrieve()`, `rag/service.py:40` `RagService.retrieve_for_execution()`.
- **Fase 4 — agente y gateway:** `agents/adapter.py:20` `LLMRequest/LLMResponse`, `agents/gemini.py:20` `GeminiAdapter`, `agents/deepseek.py:20` `DeepSeekAdapter` (fallback), `agents/factory.py:40` `LLMFactory.generate_with_fallback()`, `tools/gateway.py:80` `ToolGateway.call()` (allowlist por `ExecutionState`, budgets, idempotencia), `workflows/graph.py:30` 14 nodos con `duration_ms`/`tokens` y recálculo determinista.

## Boundary Agent Station

- Cliente: `src/procurement_platform/integrations/agent_station/client.py`
- DTOs externos: `dtos.py`
- Fake: `fake.py` + `fake_server.py` (puerto 8001)
- Docs: `docs/architecture/boundary-agent-station.md`

## Roadmap

- Fase 2: Dominio determinista — ✅ (51 tests)
- Fase 3: RAG seguro — ✅ (79 tests)
- Fase 4: Runtime agente y grafo — ✅ (99 tests: LLM Gemini→DeepSeek→fake, gateway, graph 14 nodos, recálculo determinista, budgets, validación)
- Fase 5: Human approval + idempotencia completa
- Fase 6-11: Evaluación, seguridad, observabilidad, GCP staging, hardening

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
