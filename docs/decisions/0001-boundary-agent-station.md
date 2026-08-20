# ADR 0001 — Boundary con Agent Station

**Fecha:** 2026-08-20  
**Estado:** Aceptada  
**Decisores:** equipo plataforma  
**Fase:** 0

## Contexto

Agent Station ya existe y queda fuera de este repositorio (Plan §0). Necesitamos definir cómo se comunican ambos sistemas sin acoplamiento.

Opciones consideradas:
1. Compartir modelos y DB (descartado explícitamente por el Plan).
2. SDK importado de Agent Station.
3. Cliente HTTP aislado con DTOs propios + fake.

## Decisión

Adoptamos **opción 3**:

- Cliente HTTP aislado en `src/procurement_platform/integrations/agent_station/`.
- DTOs externos versionados (`dtos.py`) separados de modelos internos (`domain/models.py`).
- Configuración por entorno: `AGENT_STATION_BASE_URL`, `AGENT_STATION_API_TOKEN`, `AGENT_STATION_CALLBACK_ENABLED`, `AGENT_STATION_TIMEOUT_MS`, `PLATFORM_CALLBACK_TOKEN`.
- Validación estricta de schemas en entrada/salida (Pydantic).
- Retries solo transitorios, circuit breaker, `Idempotency-Key`, propagación `X-Request-Id` / `traceparent`.
- Fake in-memory para desarrollo y CI; opcional servidor HTTP fake en `docker-compose` (`agent-station-fake`).

## Consecuencias

- **Positivas:** desacoplamiento total, pruebas contractuales sin dependencia externa, cambio de contrato externo solo toca adapter.
- **Negativas:** mantener DTOs duplicados; necesidad de sincronizar versionado si Agent Station evoluciona.
- **Mitigación:** contract tests y `version` field en DTOs; `Accept: application/json; version=...`.

## Cumplimiento del Plan

- §0 Boundary, §15 API inicial, §27 checklist Fase 0.
