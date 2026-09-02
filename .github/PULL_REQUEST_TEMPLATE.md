<!-- Fase 11 OSS 1.0 template -->
# PR — Enterprise Agentic AI Platform

## Descripción
Closes #

## Checklist DoD (Fase 11)

- [ ] contrato Pydantic + owner
- [ ] tests unit/integ (coverage ≥85% domain)
- [ ] manejo errores/timeout/retry
- [ ] audit + trace `trace_id`
- [ ] auth/tenant check
- [ ] caso eval si cambia comportamiento agente (`evals/procurement/*.json` o `evals/expense/*.json`)
- [ ] coste/límites documentados
- [ ] migración reversible `alembic upgrade/downgrade`
- [ ] sin PII/secrets en logs (redact)
- [ ] rollback probado
- [ ] README/ADR actualizado
- [ ] `make lint` + `pytest -q` + `python tools/openapi_lint.py --check` verde

## Cambios
- Domain: procurement | expense | platform | infra
- Herramientas: ¿nueva tool via `platform/tools/registry`?

## Métricas
- `pytest -q`: 
- `make eval` 22/22:
- `scorecard`: `code_shared %` / `unsafe 0` / `duplicate 0`
