# Arquitectura — Fase 0-4

## Objetivo Fase 1-4

Servicio FastAPI que valida contratos, persiste ejecuciones, expone ciclo de vida **determinista sin LLM para cálculos críticos** (Fase 2), **RAG seguro** (Fase 3) y **runtime de agente con Gemini → DeepSeek fallback** (Fase 4).

## Componentes entregados

```
FastAPI (src/procurement_platform/api/main.py)
  ├─ domain/models.py       Contratos Pydantic versionados
  ├─ domain/inventory.py    Cálculo determinista de faltantes, unidades, coverage, duplicados (Fase 2)
  ├─ domain/suppliers.py    SupplierCatalog determinista, best_quote, build_proposal_lines (Fase 2)
  ├─ policies/engine.py     Policy checks puros (budget, moneda, supplier, duplicado) (Fase 2)
  ├─ rag/                   RAG seguro: models, FakeEmbedder (384), security, ingestion, retrieval (Fase 3)
  ├─ agents/                Adapters LLM: Gemini (primary), DeepSeek (fallback), Fake (CI), prompts versionados, factory con fallback (Fase 4)
  ├─ tools/                 Gateway: definitions, allowlist por estado, budgets, validación, idempotencia (Fase 4)
  ├─ persistence/           SQLAlchemy + Alembic (workflow_executions, inventory_*, suppliers, purchase_*, documents, document_chunks) + pgvector
  ├─ workflows/orchestrator.py  Runtime propio — RAG en POLICY_RETRIEVED + LLM en PROPOSAL_DRAFTED (recalcula totales, no confía en total del LLM)
  ├─ workflows/graph.py     14 nodos: intake → normalize (LLM) → load_inventory → retrieve_policies → validate_evidence → calculate_shortage → query_suppliers → draft_proposal (LLM+gateway) → policy_checks → route → wait → execute → verify → summarize
  ├─ audit/service.py       Append-only audit events (node.*.completed, proposal.drafted, rag.*, tool.*)
  ├─ integrations/agent_station/  Boundary externo (client + fake)
  └─ config/settings.py     Configuración tipada (LLM provider, budgets, prompt/graph versions)
```

## Flujo Fase 2-4 (determinista + RAG + agente)

`POST /v1/procurement/executions` (con `items=[{sku,qty,unit}]` o `raw_intent` ambiguo) →
`RECEIVED` →
`NORMALIZED` (**Fase 4:** `normalize_request` usa LLM si `raw_intent` sin `items`; fallback determinista) →
`CONTEXT_LOADED` (gateway `get_inventory`) →
`POLICY_RETRIEVED` (gateway `retrieve_policy` + `RagService` con filtros tenant/vigencia/jurisdicción, citas, `should_block` si malicioso/conflicto → `BLOCKED`) →
`SHORTAGE_CALCULATED` (gateway `calculate_shortage` → `shortage = max(0, demand_total - total_available)`) →
`SUPPLIERS_QUERIED` (gateway `search_suppliers` con budget `max 5`) →
`PROPOSAL_DRAFTED` (**Fase 4:** LLM `draft_proposal` con schema `supplier_id/lines/evidence/confidence`, valida schema, **recalcula** `subtotal/total` determinísticamente, no confía en total del LLM; evidencia incluye `provider/model/was_fallback`) →
`POLICY_CHECKED` (gateway + `run_policy_checks` budget/unidad/moneda/supplier) →
`AWAITING_APPROVAL` / `BLOCKED` (si `budget_exceeded`, `not_allowed`, `is_malicious`, `has_conflict`) →
`POST /v1/approvals/{id}/decision` → `APPROVED → ACTION_EXECUTED` (gateway `submit_purchase_order` con aprobación) → `VERIFIED → COMPLETED`.

Cada nodo registra `audit_events` (`node.*.completed`, `proposal.drafted` con `model/usage`, `rag.*`, `tool.*`) + `WorkflowCheckpoint` con `duration_ms`, `model`, `tokens`. Criterios: flujo feliz produce propuesta válida con `total` recalculado; salida inválida del LLM se corrige/reintenta limitado o bloquea sin efecto externo.

Ejemplo Fase 4:
- `POST /v1/procurement/executions` con `raw_intent` ambiguo → `normalize_request` LLM propone `items`, sistema valida.
- `draft_proposal` LLM propone `supplier_demo` con `confidence 0.95`, sistema valida `supplier_id` activo, recalcula `total = 138*10.00 = 1380`, ignora `total` del LLM, registra `was_fallback` si Gemini→DeepSeek→fake.
- Si LLM devuelve `invalid_json` o `missing_fields` → validación falla, se usa fallback determinista sin efecto externo; si `budget_exceeded` (10 items con `max 5`) → `BLOCKED` con `tool.budget_exceeded`.

## Persistencia

- **PostgreSQL + pgvector** en Docker; **SQLite** para tests/CI (embeddings `fake-384` como JSON).
- Migraciones: `001_initial` (workflow), `002_inventory_domain` (inventory/demand/suppliers/orders), `003_rag_documents` (documents/document_chunks).
- Redis opcional; idempotency en Postgres + gateway cache.
- Fixtures deterministas en `evals/fixtures/`.

## Observabilidad Fase 4

- Middleware `X-Request-Id` / `traceparent` + JSON logs.
- `audit_events` correlaciona `request_id → execution_id → approval_id → trace_id` + `details` con `model`, `provider`, `was_fallback`, `tokens`, `latency_ms`, `budget`, `citations`, `tool` allowlist.
- OpenTelemetry stubs.

## Decisiones

- ADR 0001: boundary Agent Station.
- ADR 0002: runtime propio (no LangGraph obligatorio).
- ADR 0003: stack y convenciones.
- ADR 0004: dominio determinista Fase 2.
- ADR 0005: RAG seguro Fase 3.
- ADR 0006: runtime agente Fase 4 (Gemini primary → DeepSeek fallback → fake, prompts versionados, gateway con budgets, 14 nodos, recálculo determinista).

## Gaps hacia Fase 5

- Human approval con reanudación durable, expiración, idempotencia completa y verificación post-acción.
- Ver `PLAN_IMPLEMENTACION.md` §19 Fase 5.
