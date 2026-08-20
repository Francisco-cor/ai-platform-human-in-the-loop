# Arquitectura — Fase 0-2

## Objetivo Fase 1-2

Servicio FastAPI que valida contratos, persiste ejecuciones y expone ciclo de vida **determinista sin LLM para cálculos críticos** (Fase 2).

## Componentes entregados

```
FastAPI (src/procurement_platform/api/main.py)
  ├─ domain/models.py       Contratos Pydantic versionados
  ├─ domain/inventory.py    Cálculo determinista de faltantes, unidades, coverage, duplicados (Fase 2)
  ├─ domain/suppliers.py    SupplierCatalog determinista, best_quote, build_proposal_lines (Fase 2)
  ├─ policies/engine.py     Policy checks puros (budget, moneda, supplier, duplicado) (Fase 2)
  ├─ persistence/           SQLAlchemy + Alembic (workflow_executions, inventory_items, demand_forecasts, suppliers, purchase_orders, ...)
  ├─ workflows/orchestrator.py  Runtime propio — ahora _build_deterministic_proposal() con fallback sintético
  ├─ audit/service.py       Append-only audit events
  ├─ integrations/agent_station/  Boundary externo (client + fake)
  └─ config/settings.py     Configuración tipada por entorno
```

## Flujo determinista Fase 2

`POST /v1/procurement/executions` (con `items=[{sku,qty,unit}]`) →
`RECEIVED` →
`NORMALIZED` →
`CONTEXT_LOADED` (snapshots + forecasts + open_orders) →
`SHORTAGE_CALCULATED` (`calculate_shortages()` → `shortage = max(0, demand_total - total_available)`, `total_available = (on_hand - reserved) + snapshot.in_transit + Σ open_orders arrival≤horizon`) →
`SUPPLIERS_QUERIED` (`SupplierCatalog.search()` filtra por tenant/location/moneda/min-max, ordena por price+lead_time) →
`PROPOSAL_DRAFTED` (`qty = max(shortage, requested)`, `total = round(qty*price,2)`, `scope_hash`) →
`POLICY_CHECKED` (`run_policy_checks()` budget/unidad/moneda/supplier) →
`AWAITING_APPROVAL` →
`POST /v1/approvals/{id}/decision` → `APPROVED → ACTION_EXECUTED → VERIFIED → COMPLETED`.

Cada transición registra `audit_events` + `workflow_checkpoints`. Cálculos críticos no llaman a LLM (criterio salida Fase 2: mismos fixtures → mismo qty/total).

Ejemplo determinista (fixtures `evals/fixtures/inventory_happy_path.json` + `open_orders.json`):
- MAT-001: `on_hand 20 - reserved 5 =15`, `in_transit 0` + `open 15 (arrival 5)` → `total 30`, `demand 8*21=168` → `shortage 138` → `proposal qty 138` (max 138,120) → `total 138*10.00=1380`.

## Persistencia

- **PostgreSQL + pgvector** en Docker; **SQLite** para tests/CI (mismo SQLAlchemy, `Base.metadata.create_all`).
- Migraciones Alembic: `001_initial.py` (workflow), `002_inventory_domain.py` (inventory, demand, suppliers, orders).
- Redis opcional (no fuente de verdad); idempotency en Postgres.
- Fixtures deterministas en `evals/fixtures/` (inventory, demand, suppliers, open_orders).

## Observabilidad Fase 2

- Middleware `X-Request-Id` / `traceparent` + JSON logs (structlog).
- `audit_events` correlaciona `request_id → execution_id → approval_id → trace_id` + `details` con shortages.
- OpenTelemetry stubs (exporter `none` por defecto).

## Decisiones

- ADR 0001: boundary Agent Station (cliente aislado).
- ADR 0002: runtime propio Fase 1, LangGraph evaluado en Fase 4.
- ADR 0003: stack y convenciones.
- ADR 0004: dominio determinista Fase 2 (inventory/suppliers/policies sin LLM).

## Gaps hacia Fase 3

- RAG seguro con pgvector, filtros tenant/vigencia y casos maliciosos.
- Ver `PLAN_IMPLEMENTACION.md` §19 Fase 3.
