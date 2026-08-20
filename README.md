# Enterprise Agentic AI Platform — Procurement (Human-in-the-Loop)

Plataforma independiente de **Agent Station** para ejecutar workflows multi-etapa con estado durable, tool gateway, RAG seguro y aprobación humana.

> **Boundary:** Agent Station es sistema externo. Comunicación exclusivamente por APIs versionadas (`/v1`) y eventos. Ver `docs/architecture/boundary-agent-station.md`.

## Estado actual — Fase 0 y 1 completadas (2026-08-20)

| Fase | Objetivo | Estado | Criterio de salida verificado |
|------|----------|--------|-------------------------------|
| 0 — Reconocimiento y baseline | Contrato externo, fake, decisiones | ✅ | `docker compose up` levanta fake; docs explican boundary |
| 1 — Esqueleto ejecutable | Servicio FastAPI, contratos, persistencia, ejecución sintética | ✅ | `POST /v1/procurement/executions` → `AWAITING_APPROVAL` → `COMPLETED` tras aprobación |

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
  api/            FastAPI
  domain/         Contratos Pydantic (ExecutionState, Proposal, Approval, AuditEvent)
  workflows/      Orchestrator (runtime propio Fase 1; LangGraph evaluado en Fase 4)
  integrations/agent_station/  Cliente aislado + DTOs externos + fake
  persistence/    SQLAlchemy + Alembic
  config/         Settings tipadas
  evals/          Harness Fase 1
docs/
  architecture/boundary-agent-station.md
  decisions/0001-0003
evals/procurement/happy_path.json
```

## Contratos principales

- `ExecutionState`: RECEIVED → NORMALIZED → ... → AWAITING_APPROVAL → APPROVED → ACTION_EXECUTED → VERIFIED → COMPLETED (ver `domain/models.py:19`)
- `POST /v1/procurement/executions` (Idempotency-Key), `GET /v1/procurement/executions/{id}`, `GET .../events`, `POST /v1/approvals/{id}/decision`
- `AuditEvent` append-only con `trace_id` y hashes.

## Boundary Agent Station

- Cliente: `src/procurement_platform/integrations/agent_station/client.py`
- DTOs externos: `dtos.py`
- Fake: `fake.py` + `fake_server.py` (puerto 8001)
- Docs: `docs/architecture/boundary-agent-station.md`

## Roadmap

- Fase 2: Dominio determinista (inventario, faltantes, proveedores, policy checks)
- Fase 3: RAG seguro
- Fase 4: Grafo con Gemini adapter
- Fase 5: Human approval + idempotencia completa
- Fase 6-11: Evaluación, seguridad, observabilidad, GCP staging, hardening

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
