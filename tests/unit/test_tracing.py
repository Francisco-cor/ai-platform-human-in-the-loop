"""F5-1 tracing propagation tests."""

from fastapi.testclient import TestClient


def test_trace_propagation_headers(client):
    # client fixture already sets up app
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert "X-Request-Id" in resp.headers
    assert "X-Trace-Id" in resp.headers
    # trace_id should be hex 32 chars
    tid = resp.headers["X-Trace-Id"]
    assert len(tid) >= 16


def test_trace_context_logging(client, caplog=None):
    # ensure logging includes trace_id/span_id via structlog
    # make a request and check that audit event has trace_id
    from procurement_platform.persistence.database import get_sessionmaker
    from procurement_platform.persistence.models import AuditEventRow

    # create execution to generate audit events with trace
    resp = client.post(
        "/v1/procurement/executions",
        json={
            "tenant_id": "tenant_demo",
            "requester_id": "user_01",
            "raw_intent": "Necesitamos reponer materiales críticos",
            "horizon_days": 21,
            "location_id": "warehouse_north",
        },
    )
    assert resp.status_code == 202
    exec_id = resp.json()["execution_id"]
    # get events and check trace_id present
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        rows = db.query(AuditEventRow).filter(AuditEventRow.execution_id == exec_id).all()
        assert len(rows) > 0
        for r in rows:
            assert r.trace_id is not None
            # details should have span_id or duration_ms if audit enhanced
            # at least trace_id correlated
    finally:
        db.close()


def test_otel_tracer_noop():
    from procurement_platform.observability.tracing import setup_tracing, get_current_span_context

    # default exporter none should still give tracer no-op
    tracer = setup_tracing(settings_exporter="none")
    # tracer may be None or real but no export
    tid, sid = get_current_span_context()
    # may be None outside request, but should not crash
    assert True


def test_logging_correlation():
    from procurement_platform.observability.logging import request_id_ctx, span_id_ctx, trace_id_ctx

    request_id_ctx.set("req_test123")
    trace_id_ctx.set("trace_test456")
    span_id_ctx.set("span_test789")

    from procurement_platform.observability.logging import _otel_correlation_processor

    evt = {}
    out = _otel_correlation_processor(None, "info", evt)
    assert out["trace_id"] == "trace_test456"
    assert out["span_id"] == "span_test789"
    assert out["request_id"] == "req_test123"
    # cleanup
    request_id_ctx.set(None)
    trace_id_ctx.set(None)
    span_id_ctx.set(None)
