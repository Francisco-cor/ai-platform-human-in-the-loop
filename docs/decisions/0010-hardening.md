# ADR 0010 — Hardening de cimientos (calidad, DX, confiabilidad) Fase 1 Elevación

**Fecha:** 2026-09-01
**Estado:** Aceptada
**Fase:** 1 Elevación — Cimientos
**Relacionada:** PLAN_ELEVACION_11_FASES.md Fase 1

## Contexto

Scaffold Fase 0–7 demostraba flujo feliz y seguridad adversarial, pero con deuda que impide iterar rápido:
- `persistence/database.py:22` sin pooling/timeout, `sqlite` con lock en Windows (`tests/conftest.py:30` PermissionError en suite completa)
- Locks distribuidos en memoria en 3 lugares (`approvals/service.py:34`, `tools/gateway.py:83`, `workflows/orchestrator.py:241`) sin abstracción
- API sin streaming guard (`api/main.py:58` solo `content-length`) y paginación naive (`api/main.py:325` cursor solo timestamp)
- Dockerfile single-stage root (`Dockerfile:1`) sin healthcheck ni .dockerignore
- Tooling: `pyproject.toml:42` sin `ruff format`, sin `coverage`, sin `pre-commit`, sin `make check`

Criterio Fase 1: `make check` (lint+format+type+test) en <60s, coverage ≥85% domain, imagen non-root <250MB.

## Decisión

**Tooling**
- `pyproject.toml:26` add `pytest-cov, pre-commit, types-redis` dev; `tool.ruff.format` double quotes; `tool.coverage` 85% fail_under; `tool.mypy` strict (warn_unused_ignores etc)
- `.pre-commit-config.yaml` (trailing, yaml, ruff, ruff-format, mypy mirrors)
- `Makefile:9` add `format-check, type, check, test-cov, pre-commit-* , docker-scan, openapi-check` y alias `check = lint+format-check+type+test`

**Persistencia**
- `persistence/database.py:15` pg pooling `pool_size 10, max_overflow 10, recycle 3600, timeout 10, pre_ping` + `statement_timeout 5s` via `options`; sqlite `check_same_thread False` + `expire_on_commit False` en sessionmaker; helper `check_db_connection` para /readyz
- `tests/unit/test_db_pool.py` verifica sqlite/pg pooling
- `tests/conftest.py:26` windows-safe teardown (dispose+drop_all+retry) para evitar WinError 32 lock

**Locks**
- Nuevo `infra/locks/manager.py` con `MemoryLockManager` (threading.Lock per key, guard) y `RedisLockManager` lazy (SET NX PX, timeout 0.2s, fallback memory) + singleton `get_lock_manager` que en `ci/localhost` usa memory directo para no pagar 1s ping por test (evita suite 130s→19s)
- Refactor `approvals/service.py:34`, `tools/gateway.py:83`, `workflows/orchestrator.py:241` para delegar a `LockManager` con fallback a legacy threading si manager no disponible; mantiene `_locks` dict para compatibilidad `tests/conftest _reset_gateway_global`
- `tests/unit/test_locks_abstraction.py` cubre memory, singleton, orchestrator y approval paths; `tests/conftest` resetea manager per test

**API**
- `api/main.py:58` middleware ahora lee `await request.body()` para chunked streaming y enforces `max_payload_bytes` incluso sin `content-length`; retorna 413 con actual size
- `api/main.py:302` `list_events` clamp limit 1..100, stable ordering `timestamp asc, event_id asc`, retorna `total, limit, has_more`, cursor estable `timestamp > or = and id >`, `next_cursor` solo si `has_more`
- `tests/contract/test_pagination.py` verifica estable, clamp 100/1 y payload 300KB → 413

**Docker**
- `Dockerfile:1` multi-stage builder+wheels, runtime non-root `app`, labels OCI, `HEALTHCHECK curl /healthz`, chown; `.dockerignore` ignora caches, db, pgdata, env, reports

## Consecuencias

- Suite 171 tests (162→171) `make check` pasa: ruff 0, format 88 files, mypy 0, pytest 18s (sin 130s), coverage gate listo
- API resiste chunked 300KB y paginación estable (cursor no pierde eventos con mismo timestamp)
- Locks preparados para Redis en Fase 2 sin romper tests (manager lazy, ci= memory)
- Imagen lista para trivy scan y promoción a staging Fase 10

## Alternativas descartadas

- `NullPool` para sqlite global: se mantiene default pero con `pre_ping` y `expire_on_commit False` para checkpoints durables; NullPool rompería transacciones en tests con `sessionmaker`.
- `asyncio.Lock` para orchestrator: se mantiene `threading.Lock` porque orchestrator actual es sync; migración a async vendrá Fase 2 con ARQ.
- Validar payload con `app.add_middleware(MaxBodySize)` externo: se implementa en middleware propio para tener control `request_id` en error body.

## Próximos pasos

- Fase 2: ARQ worker + outbox + RedisLockManager real (saas) con ping 0.2s ya preparado; no necesita cambiar callers.
- Fase 5: dashboards usarán `check_db_connection` en /readyz.

## Definition of Done v2 (actualizada)

Una funcionalidad no está terminada hasta:
- contrato Pydantic/JSONSchema + owner
- tests unit+integ proporcionales al riesgo (coverage 85% domain)
- manejo errores, timeout, retry definido
- audit + trace con `trace_id`
- auth/tenant check
- caso eval si cambia comportamiento agente
- coste/límites documentados
- `make check` verde (lint+format+type+test)
- migración reversible y probada `alembic upgrade/downgrade`
- sin PII/secrets en logs (redact)
- rollback probado (docker previous tag)
- README/ADR actualizado

