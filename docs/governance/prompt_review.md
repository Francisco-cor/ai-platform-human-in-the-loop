# Prompt Governance — LLMOps Review Policy (Fase 6)

> **Objetivo:** evitar drift de prompts que afecte decisiones críticas (aprobación, presupuesto, allowlist) sin trazabilidad.

## Principio

- **Prompts proponen, el sistema decide.** Reglas críticas viven en `policies/engine.py` y `tools/gateway.py`, no en prompt text.
- Todo cambio de prompt debe ser **versionado, hasheado y auditado** (`prompt_hash = sha256(file)` en `audit.model_metadata` y BigQuery).
- Cambios que tocan **approval / budget / allowlist / scope_hash** requieren **ADR** y etiqueta `prompt-review`.

## Registry

- `prompts/registry/procurement-v1.yaml` (hash `sha256:...`) y `procurement-v2.yaml`.
- Loader `src/procurement_platform/agents/prompts.py:get_prompt(version, key, expected_hash)` valida hash.
- `GET /v1/procurement/executions/{id}/events?format=trace` expone `model_metadata {prompt_version, prompt_hash, graph_version}`.
- BigQuery dataset `procurement_ops` mantiene `prompt_hash` por `execution_id` para linaje.

## Review obligatoria si...

Un PR que modifica `prompts/registry/*.yaml` o `src/procurement_platform/agents/prompts.py` **requiere revisión `prompt-review`** si el diff contiene:

- `approve` / `approval` / `scope_hash` / `allowlist` / `budget` / `supplier` allowlist / `policy` allowlist
- añade herramienta nueva o modifica `draft_proposal` para incluir decisión financiera directa
- cambia `system` prompt para intentar bypass de gateway (ej "ignore previous", "you are admin")

`tools/prompt_lint.py` detecta estas palabras y bloquea CI si no hay ADR.

## Proceso

1. Crear/actualizar `prompts/registry/procurement-v{n}.yaml` + bump `PROCUREMENT_PROMPT_VERSION` en `config/settings.py` o `.env`.
2. Crear ADR en `docs/decisions/00XX-prompt-v{n}.md` explicando: qué cambió, por qué, métricas A/B esperadas, riesgo y mitigación.
3. Ejecutar `make eval-prompt-ab` (compara v1 vs v2: debe mostrar `success` no cae >5%, `hallucination_rate` estable).
   ```bash
   python -m procurement_platform.evals.runner --prompt-a procurement-v1 --prompt-b procurement-v2 --gate-ab
   ```
   Gate falla si `success` cae >5% sin ADR.
4. Ejecutar `python tools/prompt_lint.py` (o `make prompt-lint`).
5. PR debe tener etiqueta `prompt-review` y aprobación de `approver` con rol `admin`.

## Métricas A/B

- `task_success_rate`, `tool_call_accuracy` (hallucination proxy = 100 - tool_call_accuracy), `latency p95`, `cost avg`, `unsafe` y `diff_cases` con cambio de `terminal_state`.
- Reporte `evals/reports/prompt_ab.json` versionado; gate bloquea regresión >5%.

## Cache y budgets

- `agents/cache.py:LLMCache` Redis TTL 1h por `tenant+prompt_version+model`. Hit rate visible en `/metrics` (`llm_cache_hit_rate`) y Grafana.
- Per-tenant budgets `PROCUREMENT_TENANT_LLM_CONFIG='{"tenant_demo":{"models":["gemini","fake"],"max_tokens":8000}}'` enforced en `agents/factory.py` + `workflows/orchestrator.py:_check_budget_or_raise`; `security/rate_limiter.py` key `llm:{tenant}:tokens`.

## Checklist PR prompt

- [ ] `prompts/registry/*.yaml` + hash actualizado (no hardcodear hash, se calcula).
- [ ] `src/procurement_platform/agents/prompts.py` loader pasa `pytest tests/unit/test_prompt_registry.py`
- [ ] ADR en `docs/decisions/` + `prompt_review` etiqueta.
- [ ] `make eval-prompt-ab` pasa o delta justificado en ADR.
- [ ] `python tools/prompt_lint.py --strict` pasa.
- [ ] Audit muestra `prompt_hash` en `GET .../events?format=trace` y `llm_matrix` no regresa.

## Referencias

- `src/procurement_platform/agents/prompts.py:1` — loader hash
- `src/procurement_platform/agents/cache.py:1` — LLMCache
- `src/procurement_platform/evals/llm_matrix.py:1` — provider matrix
- `src/procurement_platform/evals/runner.py:40` — `--prompt-a/--prompt-b --gate-ab`
- `tools/prompt_lint.py:1` — lint governance
- `config/cost_rates.yaml:1` — tarifas versionadas usadas en matrix
