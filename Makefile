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
