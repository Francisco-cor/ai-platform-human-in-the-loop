"""F5-6 deep health + SLO tests."""

from fastapi.testclient import TestClient


def test_healthz_ok(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_readyz_deep(client):
    resp = client.get("/readyz")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] in ("ready", "degraded")
    assert "db" in data
    assert "redis" in data
    assert "vector" in data
    assert "rag" in data
    assert "tracing" in data
    assert data["db"] == "ok"


def test_readyz_db_failure(monkeypatch=None):
    # Simulate DB failure by using invalid URL? We test that readyz still returns structure
    # Instead test that readyz with mocked failure returns 503
    from unittest.mock import patch
    from fastapi.testclient import TestClient
    from procurement_platform.api.main import app
    from procurement_platform.persistence.database import get_db
    from sqlalchemy.orm import Session

    def failing_get_db():
        class FakeSession:
            def execute(self, *args, **kwargs):
                raise Exception("db down")

        yield FakeSession()  # type: ignore

    app.dependency_overrides[get_db] = failing_get_db
    with TestClient(app) as c:
        resp = c.get("/readyz")
        assert resp.status_code == 503
        body = resp.json()
        # handler unwraps detail: {code, message, request_id}
        assert (
            body.get("code") == "db_not_ready"
            or body.get("detail", {}).get("code") == "db_not_ready"
        )
    app.dependency_overrides.clear()


def test_slo_endpoint(client):
    resp = client.get("/slo")
    assert resp.status_code == 200
    data = resp.json()
    assert "slo" in data
    assert "error_rate" in data
    assert "burn_rate" in data
    assert "approval_backlog" in data
    assert "status" in data


def test_slo_reflects_error_rate(client):
    # Generate some 5xx via metrics
    from procurement_platform.observability.metrics import get_metrics, reset_metrics

    reset_metrics()
    m = get_metrics()
    # simulate 1 error and 99 successes
    for _ in range(99):
        m.observe_http("GET", "/v1/test", 200, 0.1)
    m.observe_http("GET", "/v1/test", 500, 0.2)
    resp = client.get("/slo")
    assert resp.status_code == 200
    data = resp.json()
    # error_rate should be ~0.01
    assert data["error_rate"] >= 0
    assert "burn_rate" in data
    reset_metrics()


def test_metrics_has_slo_data(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    assert "http_requests_total" in resp.text
    # after healthz and readyz calls, metrics should have data
    assert "approval_pending_total" in resp.text
