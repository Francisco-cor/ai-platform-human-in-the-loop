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

test:
	pytest -q

test-verbose:
	pytest -v

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
	python -m procurement_platform.evals.runner --dataset procurement --suite happy_path

health:
	curl -s http://localhost:8000/healthz | python -m json.tool
	curl -s http://localhost:8000/readyz | python -m json.tool

fake-agent-station:
	uvicorn procurement_platform.integrations.agent_station.fake_server:app --port 8001 --reload
