# Runbook — LLM timeout / fallback (Fase 4/6)

**Alerta:** `llm_timeout_total` ↑ o `llm_fallback_total` ↑, `tool.budget_exceeded`, p95 latency >2s.

## Diagnóstico
1. `curl /metrics | grep llm` → `llm_tokens_total{provider}`, `llm_cost_usd_total`, `llm_cache_hit_rate`.
2. `GET /v1/procurement/executions/{id}/events?format=trace` → busca `span llm.generate` con `duration_ms` y `was_fallback`.
3. Logs: `observability/logging.py` filtra `llm.timeout` con `trace_id`.
4. Grafana: `LLM provider latency` panel.

## Causas
- `GEMINI_API_KEY` inválida/expirada (401) → fallback a DeepSeek, luego Fake.
- `DEEPSEEK_BASE_URL` timeout 15s → fallback Fake determinista.
- `max_tokens_per_execution` 8000 excedido → `BudgetExhausted` bloquea antes de LLM.
- `PROCUREMENT_LLM_PROVIDER=auto` sin keys → Fake directo (CI).

## Acciones
- **Verificar keys:** `gcloud secrets versions access latest --secret gemini_api_key` (workload identity, no key file).
- **Forzar fallback:** `PROCUREMENT_LLM_PROVIDER=fake` + redeploy canary 10% → verificar `was_fallback` en audit `model_metadata.was_fallback`.
- **Budget:** `GET /v1/flags/tenant_llm_budget?tenant_id=xxx` → si `llm:tenant:tokens` rate limit, aumentar `tenant_llm_config` via `PROCUREMENT_TENANT_LLM_CONFIG` env.
- **Retrac:** `POST /v1/procurement/executions/{id}/resume` idempotente si `FAILED_RETRYABLE`.
- **Prevención:** `tools/prompt_lint.py` + `evals/llm_matrix` compara `fake` vs `gemini` coste/latencia antes de promover prompt.

**RTO:** 1m (fallback automático garantiza `total` recalculado determinista aunque LLM caiga).
