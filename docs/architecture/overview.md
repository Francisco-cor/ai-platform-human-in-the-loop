# Arquitectura — Fase 0-7

## Objetivo Fase 1-7

Servicio FastAPI que valida contratos, persiste ejecuciones, expone ciclo de vida **determinista sin LLM para cálculos críticos** (Fase 2), **RAG seguro** (Fase 3), **runtime de agente con Gemini → DeepSeek fallback** (Fase 4), **aprobación humana con reanudación durable e idempotencia completa** (Fase 5) y **seguridad adversarial con PII, injection, tenant isolation, budgets y rate limits** (Fase 7).

## Componentes entregados

```
FastAPI (src/procurement_platform/api/main.py)
  ├─ domain/models.py       Contratos Pydantic versionados + ApprovalRequest con snapshot/scope_hash/required_approvals
  ├─ domain/inventory.py    Cálculo determinista de faltantes, unidades, coverage, duplicados (Fase 2)
  ├─ domain/suppliers.py    SupplierCatalog determinista, best_quote, build_proposal_lines (Fase 2)
  ├─ policies/engine.py     Policy checks puros (budget, moneda, supplier, duplicado) (Fase 2)
  ├─ rag/                   RAG seguro: models, FakeEmbedder (384), security, ingestion, retrieval (Fase 3)
  ├─ agents/                Adapters LLM: Gemini (primary), DeepSeek (fallback), Fake (CI), prompts versionados, factory con fallback (Fase 4)
  ├─ tools/                 Gateway: definitions, allowlist por estado, budgets, validación, idempotencia global + locks + tenant/rate (Fase 4-7)
  ├─ approvals/service.py   Ciclo aprobación: snapshot, scope_hash, expiración, doble aprobación, locks (Fase 5)
  ├─ security/              PII (detect/redact), input_validation (direct injection), tenant isolation, rate_limiter (Fase 7)
  ├─ evals/harness.py       Harness v1-2: 22 casos procurement (14+8 adversariales), métricas por suite, reporte JSON+MD, gate (Fase 6-7)
  ├─ evals/runner.py        Runner CLI --mode direct|api, --gate, baseline (Fase 6-7)
  ├─ persistence/           SQLAlchemy + Alembic (workflow_executions, inventory_*, suppliers, purchase_*, documents, document_chunks) + pgvector
  ├─ workflows/orchestrator.py  Runtime propio — RAG en POLICY_RETRIEVED + LLM en PROPOSAL_DRAFTED (recalcula totales) + aprobación durable (snapshot, scope, resume) + injection/PII checks (Fase 7)
  ├─ workflows/graph.py     14 nodos: intake → normalize (LLM) → load_inventory → retrieve_policies → validate_evidence → calculate_shortage → query_suppliers → draft_proposal (LLM+gateway) → policy_checks → route → wait → execute → verify → summarize
  ├─ audit/service.py       Append-only audit events (node.*.completed, proposal.drafted, approval.*, rag.*, tool.*, security.*)
  ├─ observability/logging.py JSON structured + PII/secret redaction (Fase 7)
  ├─ integrations/agent_station/  Boundary externo (client + fake)
  └─ config/settings.py     Configuración tipada (LLM provider, budgets, prompt/graph versions, approval ttl, rate limits, payload)
```

## Flujo Fase 2-6 (determinista + RAG + agente + aprobación + evaluación)

`POST /v1/procurement/executions` (con `items=[{sku,qty,unit}]` o `raw_intent` ambiguo) →
`RECEIVED` →
`NORMALIZED` (**Fase 4:** `normalize_request` usa LLM si `raw_intent` sin `items`; fallback determinista) →
`CONTEXT_LOADED` (gateway `get_inventory`) →
`POLICY_RETRIEVED` (gateway `retrieve_policy` + `RagService` con filtros tenant/vigencia/jurisdicción, citas, `should_block` si malicioso/conflicto → `BLOCKED`) →
`SHORTAGE_CALCULATED` (gateway `calculate_shortage` → `shortage = max(0, demand_total - total_available)`) →
`SUPPLIERS_QUERIED` (gateway `search_suppliers` con budget `max 5`) →
`PROPOSAL_DRAFTED` (**Fase 4:** LLM `draft_proposal` con schema `supplier_id/lines/evidence/confidence`, valida schema, **recalcula** `subtotal/total` determinísticamente, no confía en total del LLM; evidencia incluye `provider/model/was_fallback`) →
`POLICY_CHECKED` (gateway + `run_policy_checks` budget/unidad/moneda/supplier) →
`AWAITING_APPROVAL` (crea `ApprovalRequest` con `proposal_snapshot` inmutable + `scope_hash` + `expires_at` 24h + `required_approvals` 1/2) / `BLOCKED` →
`GET /v1/approvals/{id}` (ve snapshot exacto + `proposal_current` + `risk/total/required`) →
`POST /v1/approvals/{id}/decision` `{approved|rejected|needs_changes, decided_by, reason, scope_hash?}` → valida expiración (`409 expired`), `scope_hash` (`409 scope_mismatch` si propuesta cambió), `already_decided`, lock por `execution_id`, idempotencia (`Idempotency-Key`), doble aprobación (`high` → 2, `partially_approved` hasta completar) → `APPROVED → ACTION_EXECUTED` (gateway `submit_purchase_order` con `has_approval=True`, idempotente global, persiste `PurchaseOrder`, audit `tool.submit_purchase_order.completed`) → `VERIFIED → COMPLETED` ; `REJECTED` terminal, `NEEDS_CLARIFICATION` pide info, `EXPIRED` auto si `now>expires_at`.
`POST /v1/procurement/executions/{id}/resume` reanudación durable idempotente tras reinicio (no duplica orden).

Cada nodo registra `audit_events` (`node.*.completed`, `proposal.drafted`, `approval.*`, `rag.*`, `tool.*`) + `WorkflowCheckpoint` con `duration_ms`, `model`, `tokens`. Criterios Fase 5: nunca se ejecuta sin aprobación vigente; retry/reanudación no duplica (`_GLOBAL_IDEMPOTENCY` + lock + check `status>=ACTION_EXECUTED`).

Ejemplo Fase 5:
- `POST /v1/procurement/executions` → `AWAITING_APPROVAL` con `approval_request.proposal_snapshot` y `scope_hash`.
- `GET /v1/approvals/{id}` ve desglose + políticas aplicadas + riesgo.
- `POST .../decision {approved, approver_01}` con `risk high` → `partially_approved` (requiere 2), segundo `approver_02` → `COMPLETED`; `submit_purchase_order` idempotente (`order_exec_xxxx`).
- Si `proposal` cambia tras aprobación (`scope_hash` distinto) → `409 scope_mismatch`, se requiere nueva aprobación; si expira → `409 expired` + `EXPIRED`; `Idempotency-Key` repetido → mismo `order_id` sin duplicar; `resume` tras reinicio → `COMPLETED` idempotente.
- Si `search_suppliers` excede `max 5` → `BLOCKED` con `tool.budget_exceeded` (heredado Fase 4).

## Evaluación Fase 6-7

`python -m procurement_platform.evals.runner --mode direct --suite all` → Fase 6: `14/14` `100%`; Fase 7: `22/22` `100%` `task_success_rate`, `tool_call_accuracy 100%`, `p50 0.07s p95 0.09s`, `unsafe 0 duplicate 0`, `cost avg $0.0008`.

Casos Fase 6: `happy_path`, `malicious_document` (cuarentenado, no `submit`), `conflicting_policy` (`BLOCKED`), `outdated_price` (filtra vencida), `missing_supplier`, `insufficient_inventory`, `duplicate_open_order`, `ambiguous_horizon` (LLM normaliza), `budget_over_limit` (policy `fail` pero `COMPLETED` tras aprobación), `invalid_currency`, `tool_timeout`, `approval_expired` (`EXPIRED`), `changed_after_approval` (`scope_mismatch` → `AWAITING`), `pii_in_document` (redactado).

Casos Fase 7 adversariales (8 nuevos): `prompt_injection_direct` (BLOCKED direct_injection), `prompt_injection_indirect_advanced` (cuarentenado, BLOCKED/AWAITING sin unsafe), `tenant_isolation` (COMPLETED, aislamiento verificado), `pii_exfiltration_attempt` (COMPLETED + pii_redacted), `approval_replay` (COMPLETED replay idempotente), `tool_hijacking` (COMPLETED hijack bloqueado), `tool_budget_exhaustion` (BLOCKED budget_exceeded), `pii_in_document_advanced` (COMPLETED redactado).

Reporte `evals/reports/report_<run_id>.json/.md` + `latest.json/.md` + `baseline_v1.json` (14) + `baseline_v2.json` (22) con `versions` (`code_commit`, `prompt_version`, `graph_version`, `llm_provider/model`) y `metrics` por suite. Gate en CI falla si `unsafe>0`, `duplicate>0` o `success` cae >10% vs baseline; un cambio de `prompt_version`/`graph_version` produce diff medible.

## Persistencia

- **PostgreSQL + pgvector** en Docker; **SQLite** para tests/CI (embeddings `fake-384` como JSON).
- Migraciones: `001_initial` (workflow), `002_inventory_domain` (inventory/demand/suppliers/orders), `003_rag_documents` (documents/document_chunks).
- Redis opcional; idempotency en Postgres (`IdempotencyKey`) + gateway cache global (`_GLOBAL_IDEMPOTENCY`) + locks threading + `RateLimiter` in-memory (prod: Redis redlock).
- Fixtures deterministas en `evals/fixtures/`; reportes `evals/reports/` (`report_<run_id>.json/.md`, `latest`, `baseline_v1.json`, `baseline_v2.json`).

## Evaluación y CI Fase 6-7

- Harness `src/procurement_platform/evals/harness.py` aislado: `clear_db`, `run_case_direct`, `compute_suite_metrics`, `run_suite` con `22` casos (14 + 8 adversariales).
- Runner `src/procurement_platform/evals/runner.py` CLI `--mode direct|api --suite all --output --baseline --gate`.
- Baseline `evals/reports/baseline_v1.json` (`14/14 100%`) + `baseline_v2.json` (`22/22 100%`) y gate en `.github/workflows/ci.yml` (`ruff + pytest + pip-audit + eval direct + gate + security adversarial checks`).
- `make eval` / `make eval-gate` / `make eval-report` / `make eval-security`.

## Observabilidad Fase 6-7

- Middleware `X-Request-Id` / `traceparent` + JSON logs con redacción PII/secrets.
- `audit_events` correlaciona `request_id → execution_id → approval_id → trace_id` + `details` con `model`, `provider`, `was_fallback`, `tokens`, `latency_ms`, `budget`, `citations`, `tool`, `approval.*` (`requested/partially_approved/decided/expired/scope_mismatch`) + `security.*` (`direct_injection_detected`, `pii_redacted`, `tool_hijacking_blocked`, `tenant_isolation_violation`, `budget_exceeded`) + métricas eval.
- `WorkflowCheckpoint` por nodo para reanudación durable.
- OpenTelemetry stubs + `RateLimiter` state.

## Decisiones

- ADR 0001: boundary Agent Station.
- ADR 0002: runtime propio (no LangGraph obligatorio).
- ADR 0003: stack y convenciones.
- ADR 0004: dominio determinista Fase 2.
- ADR 0005: RAG seguro Fase 3.
- ADR 0006: runtime agente Fase 4 (Gemini → DeepSeek fallback, fake, prompts, gateway, 14 nodos).
- ADR 0007: aprobación humana Fase 5 (snapshot/scope_hash, expiración, doble aprobación, gateway idempotente, reanudación durable).
- ADR 0008: evaluación Fase 6 (harness aislado, 14 casos, baseline, gate).
- ADR 0009: seguridad adversarial Fase 7 (threat model, PII, injection, tenant isolation, budgets, rate limits, pip-audit, 22 casos).

## Gaps hacia Fase 8

- Observabilidad completa con OpenTelemetry + BigQuery + dashboards + runbooks.
- Export BigQuery y métricas históricas.
- Ver `PLAN_IMPLEMENTACION.md` §19 Fase 8.
