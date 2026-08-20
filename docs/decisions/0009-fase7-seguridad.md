# ADR 0009 — Seguridad adversarial Fase 7 (threat model, PII, injection, tenant isolation, budgets, rate limits)

**Fecha:** 2026-08-20  
**Estado:** Aceptada  
**Fase:** 7

## Contexto

Fase 7 exige demostrar que los guardrails resisten ataques y datos defectuosos con criterio: *cero unsafe executions en suite adversarial y bloqueos observables/explicables*. Hasta Fase 6 existían controles básicos (RAG cuarentena, policy engine, approval scope_hash), pero faltaban: PII redaction, injection directa en `raw_intent`, tenant isolation estricta, rate limits, payload limits robustos, tool hijacking y presupuesto ampliado, además de threat model formal y dependency scanning.

## Decisión

**Threat model** (`docs/security/threat-model.md:1`): activos críticos (workflow_executions, purchase_orders, approvals, audit_events, docs, secrets), actores (requester/approver/LLM/document/Agent Station/atacante), boundaries (request→validate→trusted, RAG→quarantine→filtered, LLM→validate+recalc→trusted, tool→gateway, approval→lock+scope), STRIDE por flujo (intake, RAG, agent, approval, gateway, observabilidad), matriz de controles y riesgos residuales documentados.

**PII** (`src/procurement_platform/security/pii.py:1`): regex para email, phone, SSN, credit_card, IPv4, DNI/NIE; `detect_pii`, `redact_pii` (mask `[REDACTED_*]`), `redact_dict_values` recursivo. Integración en: `rag/ingestion.py:40` (redacta doc antes de chunking, añade flag `pii_redacted`), `workflows/orchestrator.py:300` (valida raw_intent, redacta normalized_request, audit `security.pii_redacted`), `_call_llm_for_proposal` (sanitiza prompt con `sanitize_for_llm`), `audit/service.py:20` (redacta details), `api/main.py:150` (_normalize redacta raw_intent), `observability/logging.py:20` (processor `_redact_pii_processor` redacta logs y secrets).

**Input validation** (`security/input_validation.py:1`): `validate_raw_intent` usa `detect_prompt_injection` + `detect_pii`, `should_block` si severity high/medium; `sanitize_for_llm` envuelve en `<user_data>`. Usado en `orchestrator.advance_synthetic NORMALIZED` → `BLOCKED` + `security.direct_injection_detected` si bloquea.

**Tenant isolation** (`security/tenant.py:1`): `is_tenant_allowed` estricto (cross-tenant nunca, solo mismo tenant), `filter_by_tenant`. Usado en `tools/gateway.py:30` (si payload tenant_id != gateway tenant_id → `tenant_isolation_violation`) y `rag/retrieval.py` ya filtra por tenant, `rag/service.py` verifica; tests `test_tenant_isolation_retrieval`.

**Rate limiter** (`security/rate_limiter.py:1`): sliding window in-memory por key `api:create_execution:{tenant}`, `tool:{tool}:{tenant}`; `RateLimitExceeded` con `retry_after`; singleton `get_rate_limiter`. Integrado en `api/main.py` middleware (check) y `create_execution` endpoint (hit), `tools/gateway.py` (check_and_hit por tool+tenant). Configurable via `RateLimiter(limits)`, TTL no necesario para MVP (in-memory; prod Migrar a Redis).

**Tool gateway** (`tools/gateway.py:10`): añade tenant_isolation, rate limit, PII redaction en payload log, mantiene allowlist, budgets (`ToolBudget`), idempotencia y locks. Hijacking (`admin_delete_all` unknown) → `unknown_tool` + `security.tool_hijacking_blocked` audit.

**Orchestrator** (`workflows/orchestrator.py:440`): NORMALIZED verifica `validate_raw_intent` → BLOCKED si injection; redacta PII; `POLICY_RETRIEVED` mantiene bloqueo RAG; `_call_llm_for_proposal` sanitiza con `sanitize_for_llm`; budgets: 6 items → 6 search_suppliers excede 5 → BLOCKED con `tool.budget_exceeded`.

**Casos adversariales** (`evals/procurement/*.json`): de 14 a 22 — se añaden `prompt_injection_direct` (BLOCKED direct_injection), `prompt_injection_indirect_advanced` (cuarentenado, BLOCKED/AWAITING sin unsafe), `tenant_isolation` (COMPLETED,隔离 verificado), `pii_exfiltration_attempt` (COMPLETED + pii_redacted), `approval_replay` (COMPLETED replay idempotente), `tool_hijacking` (COMPLETED hijack bloqueado), `tool_budget_exhaustion` (BLOCKED budget_exceeded), `pii_in_document_advanced` (COMPLETED redactado). Todos con `expected` estructurado y `tags phase7`.

**Harness** (`evals/harness.py:1`): extendido para ingerir docs `prompt_injection_indirect_advanced`, `tenant_other`, `pii_adv`; manejo especial `approval_replay_001` (aprueba y reintenta replay, verifica solo 1 submit) y `tool_hijacking_001` (intenta gateway hijack, audit `security.tool_hijacking_blocked`); tolerancia para quarantine (malicious/indirect sin submit → PASS aunque AWAITING, no exige rag.blocked); `prompt_injection_direct` bloqueado en orchestrator.

**Tests** (`tests/unit/test_pii.py`, `test_input_validation.py`, `test_tenant_isolation.py`, `test_rate_limiter.py`, `tests/security/test_adversarial_suite.py`, `tests/integration/test_security_adversarial.py`): 37 nuevos — PII detection/redaction, injection directa/indirecta, tenant isolation retrieval/gateway, rate limiter, payload 413, direct_injection_api BLOCKED, PII redaction API, replay idempotencia, budget exhaustion; total 162 tests (125→162).

**CI** (`.github/workflows/ci.yml:1`): `ruff check`, `pytest -q`, `pip-audit` (dependency scan, non-blocking demo), `eval harness direct suite all` → ci_report.json, `eval gate` contra `baseline_v2.json` (fallback v1), `security adversarial checks` (`pytest tests/security` + `test_security_adversarial`).

**Baseline** (`evals/reports/baseline_v2.json`): 22/22 100% `p50 0.073s p95 0.091s` `total_tokens 18700` `unsafe 0 duplicate 0` con `prompt_version procurement-v1 graph_version procurement-graph-v1`. Gate pasa; `make eval` y `make eval-gate` actualizados.

## Consecuencias

- Suite adversarial 100% con 0 unsafe/duplicate, bloqueos auditados (`security.direct_injection_detected`, `security.pii_redacted`, `security.tool_hijacking_blocked`, `rag.retrieval.blocked`, `tool.budget_exceeded`, `tenant_isolation_violation`).
- PII nunca persiste cruda ni se loguea; evidencia redactada pero trazable.
- Tenant isolation verificado en retrieval y gateway; cross-tenant →  tenant_isolation_violation.
- Rate limits y payload limits observables (429/413 con Retry-After).
- Riesgos residuales documentados (jailbreak ML classifier, DLP externo, Redis redlock futuro).
- NEXT: Fase 8 observabilidad (OTel, BigQuery, dashboards, runbooks).

## Alternativas descartadas

- DLP externo completo para PII: para MVP se usa regex; DLP cloud se reserva P2.
- Redis distribuido para rate limits/locks en Fase 7: in-memory suficiente para single-instance y tests; se migra en Fase 9 GCP.
- Bloquear PII con BLOCKED en lugar de redactar: se eligió redactar y completar (no negar servicio por PII accidental) salvo injection.
