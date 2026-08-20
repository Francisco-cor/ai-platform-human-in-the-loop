# Arquitectura — Fase 0-3

## Objetivo Fase 1-3

Servicio FastAPI que valida contratos, persiste ejecuciones, expone ciclo de vida **determinista sin LLM para cálculos críticos** (Fase 2) y **RAG seguro con defensa multicapa** (Fase 3).

## Componentes entregados

```
FastAPI (src/procurement_platform/api/main.py)
  ├─ domain/models.py       Contratos Pydantic versionados
  ├─ domain/inventory.py    Cálculo determinista de faltantes, unidades, coverage, duplicados (Fase 2)
  ├─ domain/suppliers.py    SupplierCatalog determinista, best_quote, build_proposal_lines (Fase 2)
  ├─ policies/engine.py     Policy checks puros (budget, moneda, supplier, duplicado) (Fase 2)
  ├─ rag/                   RAG seguro: models, FakeEmbedder (384), security (injection/conflicto), ingestion (quarantine), retrieval (filtros+citas) (Fase 3)
  ├─ persistence/           SQLAlchemy + Alembic (workflow_executions, inventory_*, suppliers, purchase_*, documents, document_chunks) + pgvector
  ├─ workflows/orchestrator.py  Runtime propio — ahora RAG en POLICY_RETRIEVED con bloqueo si malicioso/conflicto
  ├─ audit/service.py       Append-only audit events (rag.retrieval.completed/blocked)
  ├─ integrations/agent_station/  Boundary externo (client + fake)
  └─ config/settings.py     Configuración tipada por entorno
```

## Flujo determinista Fase 2-3

`POST /v1/procurement/executions` (con `items=[{sku,qty,unit}]`) →
`RECEIVED` →
`NORMALIZED` →
`CONTEXT_LOADED` (snapshots + forecasts + open_orders) →
`POLICY_RETRIEVED` (**Fase 3:** `RagService.retrieve_for_execution()` con filtros tenant/vigencia/jurisdicción, ranking cosine fake-384, citas `document_id/version/page/section/score`, `should_block` si `is_malicious` o `has_conflict` → `BLOCKED` con `rag.retrieval.blocked`) →
`SHORTAGE_CALCULATED` (`calculate_shortages()` → `shortage = max(0, demand_total - total_available)`) →
`SUPPLIERS_QUERIED` (`SupplierCatalog.search()` filtra por tenant/location/moneda/min-max) →
`PROPOSAL_DRAFTED` (`qty = max(shortage, requested)`, `total = round(qty*price,2)`, `scope_hash`, `evidence` con shortages) →
`POLICY_CHECKED` (`run_policy_checks()` budget/unidad/moneda/supplier) →
`AWAITING_APPROVAL` (si `should_block` es falso) / `BLOCKED` (si RAG detecta injection/conflicto) →
`POST /v1/approvals/{id}/decision` → `APPROVED → ACTION_EXECUTED → VERIFIED → COMPLETED`.

Cada transición registra `audit_events` + `workflow_checkpoints`. Cálculos críticos y RAG no llaman a LLM para decisiones (criterio salida Fase 3: bloquea caso malicioso, no ejecuta con texto no confiable, recupera evidencia relevante con trazabilidad).

Ejemplo determinista (fixtures `evals/fixtures/inventory_happy_path.json` + `open_orders.json`):
- MAT-001: `on_hand 20 - reserved 5 =15`, `in_transit 0` + `open 15 (arrival 5)` → `total 30`, `demand 8*21=168` → `shortage 138` → `proposal qty 138` → `total 1380`.

Ejemplo RAG seguro:
- Ingesta `POST /v1/documents` con `"content": "Ignore previous instructions..."` → `quarantined`, `security_flags: ["prompt_injection"]`, no indexado, `rag.retrieval.blocked` si aparece en evidencia crítica.
- `GET /v1/rag/search?query=límite&tenant_id=tenant_demo` → filtra por `tenant_demo`, `global` jurisdiction, `valid_to >= now`, excluye `is_malicious`, retorna `citation` con `score` y `reliability`.

## Persistencia

- **PostgreSQL + pgvector** en Docker (`pgvector/pgvector:pg16`); **SQLite** para tests/CI (mismo SQLAlchemy, `Base.metadata.create_all`; embeddings como JSON `fake-384`).
- Migraciones Alembic: `001_initial.py` (workflow), `002_inventory_domain.py` (inventory/demand/suppliers/orders), `003_rag_documents.py` (documents/document_chunks con índices `tenant_id`, `policy_type`, `content_hash`).
- Redis opcional (no fuente de verdad); idempotency en Postgres.
- Fixtures deterministas en `evals/fixtures/` (inventory, demand, suppliers, open_orders, document_malicious, policies_outdated).

## Observabilidad Fase 3

- Middleware `X-Request-Id` / `traceparent` + JSON logs (structlog).
- `audit_events` correlaciona `request_id → execution_id → approval_id → trace_id` + `details` con shortages y `rag.retrieval.*` (results, should_block, citations).
- OpenTelemetry stubs (exporter `none` por defecto).

## Decisiones

- ADR 0001: boundary Agent Station (cliente aislado).
- ADR 0002: runtime propio Fase 1, LangGraph evaluado en Fase 4.
- ADR 0003: stack y convenciones.
- ADR 0004: dominio determinista Fase 2 (inventory/suppliers/policies sin LLM).
- ADR 0005: RAG seguro Fase 3 (FakeEmbedder 384, defensa multicapa, quarantine, filtros previos al ranking).

## Gaps hacia Fase 4

- Runtime de agente con Gemini adapter, tool gateway, budgets y validación estructurada.
- Ver `PLAN_IMPLEMENTACION.md` §19 Fase 4.
