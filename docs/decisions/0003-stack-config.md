# ADR 0003 — Stack, configuración y convenciones

**Fecha:** 2026-08-20  
**Estado:** Aceptada  
**Fase:** 1

## Stack elegido

- **API:** FastAPI + Pydantic v2 + pydantic-settings
- **Persistencia:** SQLAlchemy 2.0 + Alembic, PostgreSQL 16 + pgvector (Docker). Fallback SQLite para tests unitarios.
- **Migraciones:** versionadas en `migrations/`, reversibles cuando sea seguro.
- **Cache / Locks:** Redis 7 (idempotency, rate limit). No fuente de verdad para aprobaciones/órdenes.
- **Observabilidad:** structlog + OpenTelemetry stubs (exporter configurable).
- **Empaquetado:** `pyproject.toml` (PEP 621), `src/` layout, paquete `procurement_platform`.
- **Infra local:** `docker-compose.yml` (postgres, redis, api, agent-station-fake).
- **Tests:** pytest, httpx, pytest-asyncio.

## Configuración tipada por entorno

`src/procurement_platform/config/settings.py` expone `Settings` con:

- `app_env: local | ci | staging | production`
- `database_url`, `redis_url`
- `agent_station_*` (ver ADR 0001)
- `log_level`, `otel_exporter`, `gcs_bucket`, `bigquery_dataset`

Variables toman prefijo `PROCUREMENT_` o `AGENT_STATION_` según dominio; `.env` soportado en local.

## Convenciones

- Paquete: `procurement_platform` (snake_case).
- Versión API: `/v1`.
- IDs: `exec_*`, `req_*`, `appr_*`, `evt_*` con `ulid` o `uuid4` prefijado.
- Timestamps: UTC ISO8601 `Z`.
- Hashes: SHA256 hex con prefijo `sha256:`.
- Errores: `{code, message, request_id, details}`.
- Logging: JSON estructurado, `request_id`, `execution_id`, `trace_id`.

## Alternativas descartadas

- `poetry` lock: se usa `pip` + `pyproject.toml` para simplicidad Fase 1; migra a `uv` si hace falta.
- `SQLModel`: se prefiere SQLAlchemy puro + Pydantic separados para mantener boundary claro.
