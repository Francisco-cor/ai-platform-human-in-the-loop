# Contributing Guide — Enterprise Agentic AI Platform

**Tiempo para levantar en <10 min (Fase 11 DoD).**

## Quickstart (sin GCP)

```bash
git clone https://github.com/anomalyco/opencode.git # o tu fork
cd ai-platform-human-in-the-loop

# 1. Instalar (venv recomendado)
python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
pip install -e sdk/python

# 2. Levantar PG+Redis+API+fake Station
docker compose up --build -d
curl http://localhost:8000/healthz  # {status: ok}
curl http://localhost:8000/readyz   # {status: ready}

# 3. Tests rápidos
pytest -q  # 329 tests <4m
python -m procurement_platform.evals.runner --mode direct --suite all  # 22/22
python -m procurement_platform.evals.rag_eval  # 50/50 precision 1.0

# 4. SDK happy path
python examples/sdk_happy.py
# o curl
bash examples/curl_happy.sh

# 5. Expense second workflow (Fase 11)
curl -X POST http://localhost:8000/v1/expense/executions -H "Content-Type: application/json" -d '{"tenant_id":"tenant_demo","requester_id":"user_01","amount":1200,"currency":"USD","reason":"viaje"}'
```

Sin Docker (SQLite):

```bash
pytest -q
pytest tests/domains/expense/test_expense_happy.py -v
```

## Estructura

```
src/procurement_platform/platform/{workflow,gateway,approvals,audit,rag,llm,tools,evals}  # core genérico >70%
src/procurement_platform/domains/procurement/{inventory,suppliers,policies}
src/procurement_platform/domains/expense/{models,policies,workflow}  # 8 nodos, reusa platform
infra/terraform/modules/{cloud_run,cloud_sql,redis,gcs,bq,iam,secrets} + envs/{staging,prod}
docs/architecture/plugins.md  # cómo añadir tool sin tocar gateway
```

## Workflow de contribución

1. Crea branch `feat/f11-...` desde `main`
2. `make lint` + `make type` + `pytest -q` + `python tools/openapi_lint.py --check`
3. Añade caso eval si cambias comportamiento agente (ver `evals/procurement/*.json`)
4. Actualiza `docs/decisions/` (ADR) si tocas contrato
5. `gh pr create --title "feat: ..." --body "Closes #..."`

## Convención commits

`feat|fix|chore|refactor|docs|test(scope): subject` (revertible, con tests).

## Definition of Done por PR

- contrato Pydantic + owner
- tests unit/integ proporcionales (coverage ≥85% domain)
- manejo errores, timeout, retry
- audit + trace `trace_id`
- auth/tenant check
- caso eval si cambia comportamiento
- coste/límites documentados
- migración reversible `alembic upgrade/downgrade`
- sin PII/secrets en logs (redact)
- rollback probado
- README/ADR actualizado

## Reportar issues

Usa `.github/ISSUE_TEMPLATE/bug_report.md` o `feature_request.md`.

## Código de conducta

Ver `CODE_OF_CONDUCT.md` (Contributor Covenant 2.1).
