# Changelog — Enterprise Agentic AI Platform

Formato Keep a Changelog + SemVer.

## [1.0.0] - 2026-09-02 — Open Source 1.0 (Fase 11)

**Plataforma demostrable pre-producción — 11 fases, 329 tests, 2 dominios, >70% código compartido.**

### Added

**Platform Core (Fase 11-1)**
- `src/procurement_platform/platform/{workflow,gateway,approvals,audit,rag,llm,tools,evals}` — core genérico domain-agnostic, no importa `domains` (`import platform` sin `procurement` ok, 1895 lines, 79% shared)
- `src/procurement_platform/domains/procurement/{inventory,suppliers,policies}` — re-export procurement domain (depende de platform, no al revés)
- `tests/unit/test_platform_core.py` — valida import platform sin procurement + registry

**Plugin Registry (Fase 11-2)**
- `platform/tools/registry.py` `register_tool(name,schema,handler)` + entry_points `procurement.tools` (`pyproject.toml`)
- `tools/builtin/calculate_shortage.py` ejemplo + `docs/architecture/plugins.md` — añadir tool sin tocar gateway

**Second Workflow (Fase 11-3)**
- `domains/expense/{models,policies/expense_policy.py,workflow 8 nodos}` — `ExpenseOrchestrator` reusa gateway/approvals/audit, `POST /v1/expense/executions {amount:1200, currency:USD, reason:"viaje"} → AWAITING → COMPLETED` con 2 aprobaciones si high (`amount>1000`), idempotente, `GET /v1/expense/executions/{id}`
- `evals/expense/happy_path.json` + `tests/domains/expense/test_expense_happy.py` 4 tests — happy 1200 (2 approvals) + 500 (1 approval) + gateway/audit reuse + code_shared
- `src/procurement_platform/api/main.py` `expense_orchestrator` + `POST/GET /v1/expense/executions` (Idempotency-Key, tenant isolation)

**Cross-domain Evals (Fase 11-4)**
- `platform/evals/harness.py` `Harness(domain="procurement|expense")` + `run_all_domains()` — `evals/{domain}/*.json`, `make eval-all-domains`
- `tests/unit/test_cross_domain.py` — procurement 22 casos + expense 1 caso

**Docs & OSS (Fase 11-5)**
- `CONTRIBUTING.md` — quickstart <10 min (docker compose + pytest)
- `CODE_OF_CONDUCT.md` — Covenant 2.1
- `.github/ISSUE_TEMPLATE/bug_report.md`, `feature_request.md`, `.github/PULL_REQUEST_TEMPLATE.md` (DoD checklist)
- `NOTICE` + `LICENSE` MIT (AGPL docs)
- `docs/api/README.md` — 33 paths OpenAPI + SDKs + Postman
- `docs/decisions/0014-second-domain.md` — ADR expense vs procurement

**Scorecard (Fase 11-6)**
- `scripts/scorecard.py` — `code_shared 79%`, `unsafe 0`, `duplicate 0`, `p95`, `cost/task`, `rag precision 1.0`, `coverage 85%` → `reports/scorecard.md` + badge README
- `make scorecard-check` gate (fail if unsafe>0 or code_shared<40)

**Release (Fase 11-7)**
- `CHANGELOG.md 1.0.0` + `docs/demos/demo_script.md` actualizado + `README` “Why not a chatbot” + “Try in 5 min”
- `make release-dry-run` — verifica `openapi.json` Spectral 0, `pytest -q`, `eval`, `scorecard`

### Changed

- `pyproject.toml` — entry_points `procurement.tools`, descripción `Fase 0-11`, markers `chaos`
- `src/procurement_platform/api/main.py` — `decide_approval` ahora soporta expense (detecta `amount` sin `supplier_id`, delega a `expense_orchestrator`)
- `Makefile` — `eval-all-domains`, `scorecard-check`, `release-dry-run`

### Platform Reusability

- `code_shared = 1895 / (1895+484) = 79.6%` (platform vs domains) — diff <30% nuevo para 2º dominio
- `POST /v1/expense/executions` reusa 70%+ código: `ToolGateway`, `ApprovalService`, `AuditEvent`, `Eval Harness` sin copiar

---

## [0.1.0] - 2026-09-02 — Fase 8 API Platform (ver docs/api/changelog.md)

## [0.1.0] - 2026-08-20 — Fases 0–7

Ver `docs/decisions/0001`–`0013` y `README.md` Roadmap.

---

## Versionado

- `1.0.0` OSS 1.0 — no breaking change vs `0.1.0` (solo adiciones backward compatible, nuevos endpoints `/v1/expense/*`)
- Breaking requiere bump `0.2.0` + `tools/openapi_lint.py --fail-on-breaking`
