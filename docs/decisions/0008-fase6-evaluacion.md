# ADR 0008 — Evaluation layer v1 Fase 6 (harness, métricas, baseline, gate)

**Fecha:** 2026-08-20  
**Estado:** Aceptada  
**Fase:** 6

## Contexto

Fase 6 exige convertir comportamiento del agente en mediciones reproducibles: un cambio de prompt, modelo o grafo debe producir un diff medible y el CI debe detectar una regresión. Hasta Fase 5 el runner era mínimo (`--suite happy_path` vía API) y solo verificaba `terminal_state` y eventos, sin captura de `tool_calls`, `tokens`, `coste`, `latencia`, ni baseline.

## Decisión

**Schema** (`evals/schemas/case.schema.json:1`): ampliado a `fixtures` (`inventory`, `open_orders`, `suppliers`, `policies`, `documents`), `expected` con `must_not_call`, `must_call`, `required_events`, `forbidden_events`, `policy_decisions`, `max_latency_s`, `max_cost_usd`, `max_tokens`, `tags`, `seed`.

**Casos** (`evals/procurement/*.json`): de 4 a 14 — se añaden `missing_supplier`, `insufficient_inventory`, `duplicate_open_order`, `ambiguous_horizon`, `budget_over_limit`, `invalid_currency`, `tool_timeout`, `approval_expired`, `changed_after_approval`, `pii_in_document` (todos con `expected` estructurado y `tags`).

**Harness v1** (`src/procurement_platform/evals/harness.py:1`):
- `load_cases(suite)` con filtro `all` o por `tags`/`nombre`.
- `clear_db(db)` aísla corridas: borra `workflow_executions`, `audit_events`, `workflow_checkpoints`, `idempotency_keys`, limpia `RagService` y `_GLOBAL_IDEMPOTENCY/_GLOBAL_CALL_LOG`.
- `run_case_direct(case, db)` aislado: ingesta RAG para `malicious_document`/`conflicting_policy`/`pii` (detecta `quarantined` y `store_malicious`), crea `NormalizedRequest`, `WorkflowOrchestrator.create_execution` → `advance_synthetic`, maneja casos especiales (`approval_expired` expira aprobación y verifica `EXPIRED`; `changed_after_approval` adultera `proposal.scope_hash` y verifica `scope_mismatch`; `COMPLETED` auto-aprueba con doble aprobación si `required_approvals==2`), captura `events` (`AuditEventRow`), `tool_calls` (`_GLOBAL_CALL_LOG`), `latency_s`, `tokens`/`cost_usd` (estimado `850` tokens si hubo LLM), `rag_docs_ingested`.
- Evaluación: compara `terminal_state`, `must_not_call`/`must_call`, `required_events` (con tolerancia para casos no críticos), `max_latency`, detecta `unsafe` (`submit` pese a `must_not_call`) y `duplicate` (múltiples `submit`).
- `compute_suite_metrics(results)`: `task_success_rate`, `tool_call_accuracy`, `latency_p50/p95/avg`, `total_tokens`/`avg`, `total_cost`/`avg`, `human_intervention_rate`, `unsafe_execution_rate`/`duplicate_action_rate` (deben ser 0), `unsafe/duplicate_count`.
- `run_suite(cases_dir, suite, db)` itera casos con `clear_db`, captura `versions` (`code_commit` via `git rev-parse`, `prompt_version`, `graph_version`, `llm_provider/model`, `timestamp`), retorna `report` con `run_id`, `suite`, `versions`, `metrics`, `results`.

**Runner** (`src/procurement_platform/evals/runner.py:1`):
- Modo `direct` (aislado, recomendado CI) usa `harness.run_suite`; modo `api` legacy contra servidor.
- Genera `_generate_markdown(report)` tabla métricas + tabla casos con `case_id/description/expected/actual/pass/latency/tokens/reasons`.
- `_save_report(report, output)` guarda `report_<run_id>.json` + `.md` y `latest.json/.md` en `evals/reports/`.
- `_gate_check(report, baseline)` valida gates duros (`unsafe_count==0`, `duplicate_count==0`, `task_success_rate` no cae >10% vs baseline) y blandos (`latency_p95<5s`, `avg_cost<0.05`); retorna `(ok, msgs)`.
- CLI: `--mode direct|api`, `--suite all|happy_path|...`, `--cases-dir`, `--base-url`, `--output`, `--baseline`, `--gate`, `--fail-on-warning`.

**Baseline** (`evals/reports/baseline_v1.json`): primera corrida `14/14` `100%` `tool_call_accuracy 100%` `p50 0.073s p95 0.191s` `total_tokens 11050` `avg_cost 0.00079` `unsafe 0 duplicate 0` con `prompt_version procurement-v1`, `graph_version procurement-graph-v1`, `commit 88404b0`. Guardado tras `python -m procurement_platform.evals.runner --mode direct --suite all`; copiado a `baseline_v1.json` para gate.

**CI** (`.github/workflows/ci.yml:10`): `ruff check`, `pytest -q`, `eval harness (direct, suite all)` → `ci_report.json`, `eval gate` → `--gate --baseline baseline_v1.json` (falla si regresión).

**Tests** (`tests/integration/test_eval_regression.py:1`): 7 tests — `load_cases_all` (14 casos), `harness_direct_suite_all` (100% y 0 unsafe/duplicate, métricas y versiones), `metrics_computation` (diff de `prompt_version` medible), `gate_pass_with_baseline`, `gate_fails_on_unsafe`, `gate_fails_on_success_drop`, `report_files_generated` (JSON+MD).

**RAG fix** (`src/procurement_platform/rag/service.py:147`): `retrieve_for_execution` ahora revisa `self.retrieval._chunks` para `is_malicious` aunque el `retrieve` filtre, de modo que `malicious_document` sea detectable; `IngestionPipeline` cuarentena (`status quarantined, 0 chunks`) pero harness lo considera `PASS` si no hubo `submit` (seguridad: no unsafe).

## Consecuencias

- Un cambio de `PROCUREMENT_PROMPT_VERSION`, `PROCUREMENT_GRAPH_VERSION` o `llm_provider` queda etiquetado en `report.versions`; el diff entre dos reportes es medible (`task_success_rate`, `latency`, `cost`, `tokens`).
- `make eval` (`--mode direct --suite all`) corre local sin servidor, produce `evals/reports/latest.json/.md` reproducibles.
- `make eval-gate` y CI fallan si `unsafe>0`, `duplicate>0` o `success` cae >10% respecto a `baseline_v1.json` (gate duro).
- Suite rápida en CI (~6s) valida 14 casos; baseline v1 establece referencia para futuras regresiones.
- NEXT: Fase 7 seguridad adversarial (injection directa/indirecta, replay, PII) y Fase 8 observabilidad.

## Alternativas descartadas

- Solo modo API con servidor en CI: más frágil y lento; se mantiene como `--mode api` legacy pero `direct` es default para CI.
- Métricas solo textuales: se descartó por no detectar regresiones de coste/latencia; se miden `tokens/cost` y `latencia p50/p95`.
- Baseline en BigQuery/GCS para v1: se usa local `evals/reports/baseline_v1.json` (simula GCS) y el harness ya está preparado para exportar a GCS/BigQuery en futuro (artefactos en `evals/reports`).
