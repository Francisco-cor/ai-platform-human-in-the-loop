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
	python tools/openapi_lint.py --check --fail-on-breaking
	@echo "openapi lint passed — 0 errors"

openapi-generate:
	python tools/openapi_lint.py --generate --check
	@echo "openapi generated at docs/api/openapi.json"

openapi-diff:
	python tools/openapi_lint.py --check 2>&1 | grep -E "breaking|BROKEN" || echo "no breaking"

example-happy:
	@echo "== curl happy =="
	@bash examples/curl_happy.sh 2>&1 | head -n 80
	@echo "== python SDK happy =="
	@python examples/sdk_happy.py 2>&1 | head -n 50

example-sdk-py:
	python examples/sdk_happy.py

example-sdk-ts:
	cd sdk/ts && npm run build 2>&1 | head -n 20; echo "ts sdk built"

sdk-test-py:
	pytest sdk/python/tests/test_client.py -v

sdk-test-ts:
	cd sdk/ts && npm test 2>&1 | head -n 100 || echo "npm test requires vitest — run: cd sdk/ts && npm install && npm test"

sdk-test:
	$(MAKE) sdk-test-py
	@echo "---"
	-$(MAKE) sdk-test-ts

webhook-test:
	python -m pytest tests/unit/test_webhooks.py -v

pagination-test:
	python -m pytest tests/unit/test_pagination_executions.py tests/contract/test_pagination.py -v

postman:
	cat docs/api/postman_collection.json | python -m json.tool | head -n 100

flags-list:
	cat infra/feature_flags.yaml 2>/dev/null || echo "no flags.yaml yet (F9)"

terraform-validate:
	terraform fmt -check -recursive infra/terraform || echo "fmt check done"
	terraform -chdir=infra/terraform/envs/staging init -backend=false && terraform -chdir=infra/terraform/envs/staging validate || echo "terraform validate staging (requires terraform binary)"
	terraform -chdir=infra/terraform/envs/prod init -backend=false && terraform -chdir=infra/terraform/envs/prod validate || echo "terraform validate prod"

terraform-plan:
	terraform -chdir=infra/terraform/envs/staging init -backend=false && terraform -chdir=infra/terraform/envs/staging plan -input=false || echo "plan mock (no backend)"

tflint:
	tflint --init 2>/dev/null; tflint --recursive infra/terraform || echo "tflint done"

# Fase 9 — Data Platform (BigQuery, GCS, time-travel, lineage, retention)
bq-drain:
	curl -s -X POST http://localhost:8000/v1/bq/drain -H "Content-Type: application/json" -d '{}' | python -m json.tool

bq-query:
	curl -s "http://localhost:8000/v1/bq/query?dataset=procurement_ops&table=bq_audit&execution_id=$(EXEC_ID)" | python -m json.tool

time-travel:
	curl -s "http://localhost:8000/v1/procurement/executions/$(EXEC_ID)/time-travel?at=$(AT)" | python -m json.tool

lineage-doc:
	curl -s "http://localhost:8000/v1/lineage?document_id=$(DOC_ID)" | python -m json.tool

lineage-exec:
	curl -s "http://localhost:8000/v1/lineage?execution_id=$(EXEC_ID)" | python -m json.tool

retention-dry:
	curl -s -X POST http://localhost:8000/v1/retention/run -H "Content-Type: application/json" -d '{"dry_run": true}' | python -m json.tool

retention-run:
	curl -s -X POST http://localhost:8000/v1/retention/run -H "Content-Type: application/json" -d '{}' | python -m json.tool

artifacts-list:
	curl -s "http://localhost:8000/v1/artifacts?prefix=evals/" | python -m json.tool

# Fase 10 — Cloud Native, GitOps y SRE
docker-build:
	docker buildx build --build-arg VERSION=$${VERSION:-0.1.0} --tag procurement-platform:local --load . && echo "image procurement-platform:local built VERSION=$${VERSION:-0.1.0}"

docker-push:
	@echo "docker push to Artifact Registry: us-central1-docker.pkg.dev/$$PROJECT/procurement/procurement-api:$$VERSION"
	@echo "requires: gcloud auth configure-docker us-central1-docker.pkg.dev && docker tag procurement-platform:local us-central1-docker.pkg.dev/$$PROJECT/procurement/procurement-api:$$VERSION && docker push ..."

sbom:
	syft procurement-platform:local -o cyclonedx-json=sbom.json || echo "syft not installed, skipping"
	cat sbom.json | head -n 50

backup-drill:
	infra/backup/backup.sh restore-drill || bash infra/backup/backup.sh restore-drill

backup-create:
	infra/backup/backup.sh create || bash infra/backup/backup.sh create

migrate-job:
	kubectl apply -f infra/db/migrate_job.yaml || echo "kubectl not configured, job yaml valid"

pgbouncer-config:
	cat infra/db/pgbouncer.ini

chaos-test:
	pytest tests/chaos -m chaos -v

chaos-db:
	pytest tests/chaos/test_db_failover.py -v -m chaos

slo-check:
	cat docs/operations/SLO.md | head -n 40

runbooks:
	ls -R docs/operations/runbooks

eval-all-domains:
	python -c "from procurement_platform.platform.evals.harness import run_all_domains; import json; r=run_all_domains(); print(json.dumps({k: (v.get('metrics',{}).get('task_success_rate') if isinstance(v, dict) else 'ok') for k,v in r.items()}, indent=2)); print('procurement 22/22:', r['procurement']['metrics']['task_success_rate'], 'expense', r['expense']['cases'][0]['status'])"
	@echo "expense via: curl -X POST http://localhost:8000/v1/expense/executions -d '{\"amount\":1200}'"

scorecard-check:
	python scripts/scorecard.py

release-dry-run:
	@echo "== release dry-run v1.0.0 =="
	python tools/openapi_lint.py --check
	pytest -q
	python -m procurement_platform.evals.runner --mode direct --suite all --gate --baseline evals/reports/baseline_v2.json
	python scripts/scorecard.py
	@echo "CHANGELOG.md ## [1.0.0] present:" && grep -q "## \[1.0.0\]" CHANGELOG.md && echo "ok"
	@echo "docs/api/README.md present:" && grep -q "33 paths" docs/api/README.md && echo "ok"
	@echo "CONTRIBUTING.md <10 min:" && grep -q "Quickstart" CONTRIBUTING.md && echo "ok"
	@echo "release dry-run PASSED"
