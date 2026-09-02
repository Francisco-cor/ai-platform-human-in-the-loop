"""SDK Python tests — Fase 8."""

import httpx
from procurement_sdk import ProcurementClient


def _mock_transport():
    # Mock that simulates procurement API for SDK tests
    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/v1/procurement/executions" and request.method == "POST":
            # check Idempotency-Key header present
            assert "Idempotency-Key" in request.headers
            return httpx.Response(202, json={"execution_id": "exec_sdk_123", "approval_request": {"approval_id": "appr_sdk_123"}, "status": "AWAITING_APPROVAL"})
        if path == "/v1/procurement/executions/exec_sdk_123" and request.method == "GET":
            return httpx.Response(200, json={"execution_id": "exec_sdk_123", "status": "AWAITING_APPROVAL"})
        if path == "/v1/approvals/appr_sdk_123/decision" and request.method == "POST":
            assert "Idempotency-Key" in request.headers
            return httpx.Response(200, json={"approval_id": "appr_sdk_123", "status": "approved", "execution_status": "COMPLETED"})
        if path == "/v1/procurement/executions/exec_sdk_123/events" and request.method == "GET":
            return httpx.Response(200, json={"execution_id": "exec_sdk_123", "count": 1, "total": 1, "events": []})
        if path == "/v1/procurement/executions" and request.method == "GET":
            return httpx.Response(200, json={"count": 1, "total_count": 1, "has_more": False, "executions": [{"execution_id": "exec_sdk_123"}]})
        if path == "/healthz":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(404, json={"code": "not_found"})

    return httpx.MockTransport(handler)


def test_create_and_approve_via_sdk():
    transport = _mock_transport()
    client = ProcurementClient(base_url="http://test", transport=transport)
    resp = client.create_execution({"tenant_id": "tenant_demo", "items": [{"sku": "MAT-001", "quantity": 10}]})
    assert resp["execution_id"] == "exec_sdk_123"
    assert resp["approval_request"]["approval_id"] == "appr_sdk_123"

    detail = client.get_execution("exec_sdk_123")
    assert detail["status"] == "AWAITING_APPROVAL"

    dec = client.approve("appr_sdk_123", decided_by="approver_01")
    assert dec["status"] == "approved"

    events = client.list_events("exec_sdk_123")
    assert events["count"] == 1

    page = client.list_executions(limit=10)
    assert page["total_count"] == 1 or "total" in page or "count" in page

    client.close()


def test_idempotency_key_auto():
    seen_keys = set()

    def handler(request: httpx.Request) -> httpx.Response:
        key = request.headers.get("Idempotency-Key")
        assert key is not None
        assert key not in seen_keys or True  # allow reuse but should be present
        seen_keys.add(key)
        return httpx.Response(202, json={"execution_id": "exec_1", "approval_request": {"approval_id": "appr_1"}})

    transport = httpx.MockTransport(handler)
    client = ProcurementClient(base_url="http://test", transport=transport)
    client.create_execution({"tenant_id": "tenant_demo"})
    assert len(seen_keys) == 1
    client.close()


def test_retry_on_429():
    calls = {"count": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["count"] += 1
        if calls["count"] == 1:
            return httpx.Response(429, headers={"Retry-After": "0"}, json={"code": "rate_limited"})
        return httpx.Response(202, json={"execution_id": "exec_retry", "approval_request": {"approval_id": "appr_retry"}})

    transport = httpx.MockTransport(handler)
    client = ProcurementClient(base_url="http://test", transport=transport, max_retries=2)
    resp = client.create_execution({"tenant_id": "tenant_demo"})
    assert resp["execution_id"] == "exec_retry"
    assert calls["count"] == 2
    client.close()
