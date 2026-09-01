# Runbook — Budget exceeded (F5-4)

**Alerta:** `budget_exceeded_total` rate >0.1/s o `POST /v1/procurement/executions` retorna `429`/`409 budget_exceeded`.

## Diagnóstico

1. **Métricas:** `curl /metrics | grep budget_exceeded` y `curl /metrics | grep llm_cost` → `llm_cost_usd_total{tenant}`.
2. **SLO:** `curl /slo` → `burn_rate` si >5.
3. **Audit:** `SELECT * FROM audit_events WHERE event_type='tool.budget_exceeded' OR details->>'reason' LIKE '%budget%'` → `execution_id`, `tenant_id`, `cost`.
4. **Config:** `cat config/cost_rates.yaml` → `version`, tarifas; `echo $PROCUREMENT_MAX_TOKENS_PER_EXECUTION` (default 8000).
5. **Tenant usage:** `GET /v1/procurement/executions/{id}` → `proposal.total` vs `policy.budget_limit`; `GET /metrics | grep cost_usd_per_execution`.

## Causas

- `max_tokens_per_execution` bajo (8000) para horizonte 21 días + 3 suppliers → LLM `prompt 2k` por nodo x14 nodos excede.
- `gemini-2.0-flash` tarifa `0.075/0.30` vs `deepseek` `0.14/0.28` diferencia `cost/task` (ver `make eval` `cost`).
- Tenant `free` plan rate limit 60/min bloquea pero no budget.

## Acciones

- **Aumentar:** `PROCUREMENT_MAX_TOKENS_PER_EXECUTION=16000` y redeploy.
- **Optimizar:** `agents/adapter.py` `estimate_cost` + `prompt_version` cache; reducir `max_context_chars` 12000 → 8000.
- **FinOps:** `cost_usd_per_execution` histogram → Grafana panel `Coste por tenant`; si `cost/task >0.01 USD` alerta.
- **Reset:** `reset_finops_state()` en `tests/conftest.py` ya limpia entre tests; prod reset via `POST /v1/admin/reset-budget` (futuro).

**Prevención:** dashboard `Coste por tenant` + alerta `budget_exceeded_total` → Slack.

