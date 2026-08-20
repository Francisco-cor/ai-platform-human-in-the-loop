# Arquitectura — Fase 0-1

## Objetivo Fase 1

Servicio FastAPI que valida contratos, persiste ejecuciones y expone ciclo de vida sintético sin modelo real.

## Componentes entregados

```
FastAPI (src/procurement_platform/api/main.py)
  ├─ domain/models.py   Contratos Pydantic versionados
  ├─ persistence/       SQLAlchemy + Alembic (workflow_executions, audit_events, idempotency_keys, checkpoints)
  ├─ workflows/orchestrator.py  Runtime propio (transiciones validadas, checkpoints)
  ├─ audit/service.py   Append-only audit events
  ├─ integrations/agent_station/  Boundary externo (client + fake)
  └─ config/settings.py Configuración tipada por entorno
```

## Flujo sintético Fase 1

`POST /v1/procurement/executions` → `RECEIVED` → `NORMALIZED` → ... → `AWAITING_APPROVAL` (con `Proposal` y `ApprovalRequest` stub) → `POST /v1/approvals/{id}/decision` → `APPROVED → ACTION_EXECUTED → VERIFIED → COMPLETED`.

Todos los estados están validados por `is_valid_transition` y cada transición registra `audit_events` + `workflow_checkpoints`.

## Persistencia

- **PostgreSQL + pgvector** en Docker; **SQLite** para tests/CI (mismo SQLAlchemy, `Base.metadata.create_all`).
- Migraciones Alembic en `migrations/versions/001_initial.py`.
- Redis opcional (no fuente de verdad); idempotency en Postgres.

## Observabilidad Fase 1

- Middleware `X-Request-Id` / `traceparent` + JSON logs (structlog).
- `audit_events` correlaciona `request_id → execution_id → approval_id → trace_id`.
- OpenTelemetry stubs (exporter `none` por defecto).

## Decisiones

- ADR 0001: boundary Agent Station (cliente aislado).
- ADR 0002: runtime propio Fase 1, LangGraph evaluado en Fase 4.
- ADR 0003: stack y convenciones.

## Gaps hacia Fase 2

- Inventario real, cálculo determinista de faltantes, simulador proveedores, policy engine.
- Ver `PLAN_IMPLEMENTACION.md` §19 Fase 2.
