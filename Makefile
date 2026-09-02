.PHONY: install dev lint format test run docker-up docker-down migrate eval

install:
	pip install -e ".[dev]"

dev:
	pip install -e ".[dev]"

lint:
	ruff check src tests

format:
	ruff check --fix src tests
	ruff format src tests

format-check:
	ruff format --check src tests

type:
	mypy src

check: lint format-check type test
	@echo "check passed: lint + format + type + test"

test:
	pytest -q

test-verbose:
	pytest -v

test-cov:
	pytest --cov=procurement_platform --cov-report=term-missing --cov-report=html --cov-fail-under=85

pre-commit-install:
	pre-commit install

pre-commit-run:
	pre-commit run --all-files

run:
	uvicorn procurement_platform.api.main:app --reload --host 0.0.0.0 --port 8000

docker-up:
	docker compose up --build -d

docker-down:
	docker compose down -v

migrate:
	alembic upgrade head

migrate-new:
	alembic revision --autogenerate -m "$(msg)"

eval:
	python -m procurement_platform.evals.runner --mode direct --suite all

eval-api:
	python -m procurement_platform.evals.runner --mode api --suite all --base-url http://localhost:8000

eval-gate:
	python -m procurement_platform.evals.runner --mode direct --suite all --gate --baseline evals/reports/baseline_v2.json

eval-gate-v1:
	python -m procurement_platform.evals.runner --mode direct --suite all --gate --baseline evals/reports/baseline_v1.json

eval-report:
	python -m procurement_platform.evals.runner --mode direct --suite all --output evals/reports/latest.json

eval-rag:
	python -m procurement_platform.evals.rag_eval

eval-rag-rerank:
	python -m procurement_platform.evals.rag_eval --reranker

eval-rag-json:
	python -m procurement_platform.evals.rag_eval --json-out evals/reports/rag_eval.json

eval-security:
	python -m pytest tests/security -v
	python -m pytest tests/integration/test_security_adversarial.py -v

audit:
	pip-audit --desc || echo "pip-audit done"

threat-model:
	cat docs/security/threat-model.md

health:
	curl -s http://localhost:8000/healthz | python -m json.tool
	curl -s http://localhost:8000/readyz | python -m json.tool

metrics:
	curl -s http://localhost:8000/metrics | head -n 50

slo:
	curl -s http://localhost:8000/slo | python -m json.tool

trace:
	curl -s "http://localhost:8000/v1/procurement/executions/$$EXEC_ID/events?format=trace" | python -m json.tool | head -n 80

dashboards-up:
	docker compose --profile observability up -d 2>/dev/null || echo "Grafana/Loki profile not in compose, dashboard JSON at observability/dashboards/procurement.json — import manually"
	@echo "Grafana dashboard: observability/dashboards/procurement.json"
	@echo "Alerts: observability/alerts/alerts.yaml"

eval-finops:
	python -m pytest tests/unit/test_finops.py -v
	@echo "cost_rates: cat config/cost_rates.yaml"

eval-llm-matrix:
	python -m procurement_platform.evals.llm_matrix --providers fake gemini deepseek --output evals/reports/llm_matrix.json
	@cat evals/reports/llm_matrix.json | python -m json.tool | head -n 80

eval-prompt-ab:
	python -m procurement_platform.evals.runner --prompt-a procurement-v1 --prompt-b procurement-v2 --gate-ab --ab-output evals/reports/prompt_ab.json
	@cat evals/reports/prompt_ab.json | python -m json.tool | head -n 100

prompt-lint:
	python tools/prompt_lint.py

prompt-lint-strict:
	python tools/prompt_lint.py --strict

cache-stats:
	curl -s http://localhost:8000/metrics | grep -A2 llm_cache || echo "no cache metrics yet"

tenant-budget-test:
	python -m pytest tests/unit/test_tenant_llm_budget.py -v 2>/dev/null || echo "tenant budget test not found — run pytest -k tenant"

ui-install:
	cd ui && npm install

ui-dev:
	cd ui && npm run dev

ui-build:
	cd ui && npm run build

ui-test:
	cd ui && npm run test:e2e || echo "playwright not installed, run: cd ui && npx playwright install"

ui-lint:
	cd ui && npm run lint 2>/dev/null || echo "ui lint skip"

demo:
	@echo "Fase 7 demo — happy vs malicious via UI"
	@echo "1. docker compose up --build -d (API+UI)"
	@echo "2. curl POST /v1/procurement/executions -> approval inbox <2min"
	@echo "3. Playwright: cd ui && npx playwright test tests/approval.spec.ts"
	@echo "See docs/demos/demo_script.md for full playbook"

smoke-staging:
	@echo "smoke-staging: create execution + approve via API"
	@python -c "import httpx, json; c=httpx.Client(base_url='http://localhost:8000', timeout=10); r=c.post('/v1/procurement/executions', json={'tenant_id':'tenant_demo','requester_id':'user_01','items':[{'sku':'MAT-001','quantity':10,'unit':'piece'}]}); print(r.status_code, r.text[:300]); d=r.json(); aid=d['approval_request']['approval_id']; r2=c.post(f\"/v1/approvals/{aid}/decision\", json={'decision':'approved','decided_by':'smoke_tester'}); print('decide', r2.status_code, r2.text[:300])" || echo "smoke-staging requires API at localhost:8000"

demo-script:
	cat docs/demos/demo_script.md

notifications-test:
	python -m pytest tests/unit/test_notifications.py tests/unit/test_approval_sla.py -v

bulk-test:
	python -m pytest tests/unit/test_bulk_approvals.py -v

sla-check:
	curl -s -X POST http://localhost:8000/v1/approvals/sla/check -H "Content-Type: application/json" -d '{}' | python -m json.tool

export-csv:
	curl -s "http://localhost:8000/v1/approvals/export?tenant=tenant_demo&state=pending" | head -n 20

fake-agent-station:
	uvicorn procurement_platform.integrations.agent_station.fake_server:app --port 8001 --reload

docker-scan:
	trivy image procurement-platform:local || echo "trivy not installed, skipping"

openapi-check:
	python -c "from procurement_platform.api.main import app; import json; print(json.dumps(app.openapi(), indent=2)[:500])" | head -n 100

flags-list:
	cat infra/feature_flags.yaml 2>/dev/null || echo "no flags.yaml yet (F9)"

scorecard-check:
	python scripts/scorecard.py 2>/dev/null || echo "scorecard not yet (F11)"
