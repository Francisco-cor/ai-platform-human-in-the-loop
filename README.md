# Enterprise Agentic AI Platform — Procurement (Human-in-the-Loop)

Plataforma independiente de **Agent Station** para ejecutar workflows multi-etapa con estado durable, tool gateway, RAG seguro y aprobación humana.

> **Boundary:** Agent Station es sistema externo. Comunicación exclusivamente por APIs versionadas (`/v1`) y eventos. Ver `docs/architecture/boundary-agent-station.md`.

## Estado actual — Fase 0, 1 y 2 completadas (2026-08-20)

| Fase | Objetivo | Estado | Criterio de salida verificado |
|------|----------|--------|-------------------------------|
| 0 — Reconocimiento y baseline | Contrato externo, fake, decisiones | ✅ | `docker compose up` levanta fake; docs explican boundary |
| 1 — Esqueleto ejecutable | Servicio FastAPI, contratos, persistencia, ejecución sintética | ✅ | `POST /v1/procurement/executions` → `AWAITING_APPROVAL` → `COMPLETED` tras aprobación |
| 2 — Dominio determinista | Inventario, faltantes, proveedores, policy checks sin LLM | ✅ | Mismos fixtures → mismo `qty`/`total`; 51 tests (inventario, suppliers, policies, determinismo, persistencia, API); cálculos críticos no llaman al modelo |

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
  api/            FastAPI (healthz, readyz, executions, approvals)
  domain/         Contratos + inventory (faltantes, unidades, coverage), suppliers (catalog, quotes), models
  policies/       Policy engine determinista (budget, moneda, supplier, duplicado)
  workflows/      Orchestrator determinista Fase 2 (shortage → supplier → proposal → policy checks)
  integrations/agent_station/  Cliente aislado + DTOs externos + fake
  persistence/    SQLAlchemy + Alembic (inventory_items, demand_forecasts, suppliers, purchase_orders)
  config/         Settings tipadas
  evals/          Harness + fixtures (inventory, suppliers, open_orders)
docs/
  architecture/boundary-agent-station.md, overview.md (Fase 0-2)
  decisions/0001-0004 (boundary, orchestrator, stack, fase2 dominio)
evals/procurement/happy_path.json
```

## Contratos principales

- `ExecutionState`: RECEIVED → NORMALIZED → ... → AWAITING_APPROVAL → APPROVED → ACTION_EXECUTED → VERIFIED → COMPLETED (ver `domain/models.py:30`)
- `POST /v1/procurement/executions` (Idempotency-Key), `GET /v1/procurement/executions/{id}`, `GET .../events`, `POST /v1/approvals/{id}/decision`
- `AuditEvent` append-only con `trace_id` y hashes.
- **Fase 2 — cálculo determinista:** `domain/inventory.py:114` `calculate_shortage_for_item()` → `shortage = max(0, demand_total - total_available)`, unidades convertibles, `domain/suppliers.py:48` `SupplierCatalog.search()`, `policies/engine.py:200` `run_policy_checks()`.

## Boundary Agent Station

- Cliente: `src/procurement_platform/integrations/agent_station/client.py`
- DTOs externos: `dtos.py`
- Fake: `fake.py` + `fake_server.py` (puerto 8001)
- Docs: `docs/architecture/boundary-agent-station.md`

## Roadmap

- Fase 2: Dominio determinista (inventario, faltantes, proveedores, policy checks) — ✅ completada (51 tests)
- Fase 3: RAG seguro (GCS → chunks → pgvector, filtros vigencia, casos maliciosos)
- Fase 4: Grafo con Gemini adapter (tool gateway, budgets, validación estructurada)
- Fase 5: Human approval + idempotencia completa
- Fase 6-11: Evaluación, seguridad, observabilidad, GCP staging, hardening

**Ejemplo determinista Fase 2:**
- Fixtures `evals/fixtures/inventory_happy_path.json` (MAT-001: on_hand 20,reserved 5 → available 15; demand 8*21=168) + `open_orders.json` (15 arrival 5) → `total_available 30` → `shortage 138` → `proposal qty 138` → `total 138*10.00=1380` (mismo fixtures → mismo resultado, sin LLM).

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
