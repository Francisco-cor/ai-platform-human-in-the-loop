# Enterprise Agentic AI Platform — Procurement (Human-in-the-Loop)

Plataforma independiente de **Agent Station** para ejecutar workflows multi-etapa con estado durable, tool gateway, RAG seguro y aprobación humana.

> **Boundary:** Agent Station es sistema externo. Comunicación exclusivamente por APIs versionadas (`/v1`) y eventos. Ver `docs/architecture/boundary-agent-station.md`.

## Estado actual — Fase 0, 1, 2 y 3 completadas (2026-08-20)

| Fase | Objetivo | Estado | Criterio de salida verificado |
|------|----------|--------|-------------------------------|
| 0 — Reconocimiento y baseline | Contrato externo, fake, decisiones | ✅ | `docker compose up` levanta fake; docs explican boundary |
| 1 — Esqueleto ejecutable | Servicio FastAPI, contratos, persistencia, ejecución sintética | ✅ | `POST /v1/procurement/executions` → `AWAITING_APPROVAL` → `COMPLETED` tras aprobación |
| 2 — Dominio determinista | Inventario, faltantes, proveedores, policy checks sin LLM | ✅ | Mismos fixtures → mismo `qty`/`total`; 51 tests; cálculos críticos no llaman al modelo |
| 3 — RAG seguro | Pipeline GCS→chunks→pgvector, filtros, citas, bloqueo malicioso | ✅ | Recupera evidencia relevante con trazabilidad, bloquea `malicious_document`, no ejecuta con texto no confiable; 79 tests (security, ingesta, retrieval, precision/recall, API, orchestrator) |

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
  rag/            RAG seguro: models, FakeEmbedder 384, security (injection/conflicto/obsolescencia), ingestion (quarantine), retrieval (filtros+citas), service
  workflows/      Orchestrator Fase 3 (RAG en POLICY_RETRIEVED con bloqueo si malicioso/conflicto)
  integrations/agent_station/  Cliente aislado + DTOs externos + fake
  persistence/    SQLAlchemy + Alembic (inventory_*, suppliers, purchase_*, documents, document_chunks) + pgvector
  config/         Settings tipadas
  evals/          Harness + fixtures (inventory, suppliers, open_orders, document_malicious, policies_outdated) + casos malicious/conflicting/outdated
docs/
  architecture/boundary-agent-station.md, overview.md (Fase 0-3)
  decisions/0001-0005 (boundary, orchestrator, stack, fase2, fase3 RAG)
evals/procurement/happy_path.json, malicious_document.json, conflicting_policy.json, outdated_price.json
```

## Contratos principales

- `ExecutionState`: RECEIVED → NORMALIZED → ... → AWAITING_APPROVAL → APPROVED → ACTION_EXECUTED → VERIFIED → COMPLETED (ver `domain/models.py:30`)
- `POST /v1/procurement/executions` (Idempotency-Key), `GET /v1/procurement/executions/{id}`, `GET .../events`, `POST /v1/approvals/{id}/decision`, `POST /v1/documents` (ingesta RAG), `GET /v1/rag/search` (citas)
- `AuditEvent` append-only con `trace_id` y hashes + `rag.retrieval.*`.
- **Fase 2 — cálculo determinista:** `domain/inventory.py:114` `calculate_shortage_for_item()`, `domain/suppliers.py:48` `SupplierCatalog.search()`, `policies/engine.py:200` `run_policy_checks()`.
- **Fase 3 — RAG seguro:** `rag/security.py:20` `detect_prompt_injection()`, `rag/ingestion.py:71` `IngestionPipeline.ingest()` (quarantine), `rag/retrieval.py:40` `RetrievalService.retrieve()` (filtros tenant/vigencia/jurisdicción antes de ranking + citas), `rag/service.py:40` `RagService.retrieve_for_execution()` (bloqueo si malicioso/conflicto).

## Boundary Agent Station

- Cliente: `src/procurement_platform/integrations/agent_station/client.py`
- DTOs externos: `dtos.py`
- Fake: `fake.py` + `fake_server.py` (puerto 8001)
- Docs: `docs/architecture/boundary-agent-station.md`

## Roadmap

- Fase 2: Dominio determinista — ✅ (51 tests)
- Fase 3: RAG seguro — ✅ (79 tests: security, ingesta, retrieval con precision/recall, API, orchestrator bloqueo)
- Fase 4: Grafo con Gemini adapter (tool gateway, budgets, validación estructurada)
- Fase 5: Human approval + idempotencia completa
- Fase 6-11: Evaluación, seguridad, observabilidad, GCP staging, hardening

**Ejemplo determinista Fase 2:**
- Fixtures `evals/fixtures/inventory_happy_path.json` (MAT-001: on_hand 20,reserved 5 → available 15; demand 8*21=168) + `open_orders.json` (15 arrival 5) → `total_available 30` → `shortage 138` → `proposal qty 138` → `total 1380`.

**Ejemplo RAG seguro Fase 3:**
- `POST /v1/documents` con `{"content":"Ignore previous instructions..."}` → `status: quarantined`, `security_flags: ["prompt_injection"]`, no indexado.
- `GET /v1/rag/search?query=límite&tenant_id=tenant_demo` → filtra `tenant_demo`, `valid_to>=now`, excluye `is_malicious`, retorna `citation {document_id, version, page/section, score, reliability}`.
- Conflicto `budget 5000 vs 1000` mismo `tenant/location` → `detect_conflict` → `BLOCKED` en `POLICY_RETRIEVED` con `rag.retrieval.blocked`.

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
