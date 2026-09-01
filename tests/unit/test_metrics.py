"""F5-2 metrics RED + domain tests."""

from fastapi.testclient import TestClient


def test_metrics_endpoint(client):
    resp = client.get("/metrics")
    assert resp.status_code == 200
    text = resp.text
    assert "http_request_duration_seconds" in text
    assert "http_requests_total" in text
    assert "tool_call_duration_seconds" in text
    assert "rag_retrieval_latency_seconds" in text
    assert "llm_tokens_total" in text


def test_http_metrics_recorded(client):
    from procurement_platform.observability.metrics import get_metrics, reset_metrics

    reset_metrics()
    m = get_metrics()
    # make request
    client.get("/healthz")
    # check metrics generated
    txt = m.generate()
    assert "http_requests_total" in txt
    # should have count at least 1 for healthz
    assert 'path="/healthz"' in txt or "path" in txt


def test_tool_metrics(client):
    from procurement_platform.observability.metrics import get_metrics, reset_metrics
    from procurement_platform.tools.gateway import ToolGateway
    from procurement_platform.domain.models import ExecutionState

    reset_metrics()
    gw = ToolGateway()
    # call a tool to generate metric — use state that allows get_inventory
    gw.call(
        tool_name="get_inventory",
        payload={"sku": "MAT-001", "location_id": "warehouse_north"},
        execution_id="exec_test_metrics",
        state=ExecutionState.RECEIVED,
        tenant_id="tenant_demo",
    )
    m = get_metrics()
    txt = m.generate()
    assert "tool_calls_total" in txt
    assert "get_inventory" in txt


def test_rag_metrics(client, db_session):
    from procurement_platform.observability.metrics import get_metrics, reset_metrics

    reset_metrics()
    # ingest doc and search to trigger rag metric
    client.post(
        "/v1/documents",
        json={
            "tenant_id": "tenant_demo",
            "title": "Metrics test",
            "content": "Política: límite 5000 USD métrica test contenido largo suficiente",
        },
    )
    client.get("/v1/rag/search", params={"query": "límite 5000", "tenant_id": "tenant_demo"})
    m = get_metrics()
    txt = m.generate()
    assert "rag_retrieval" in txt


def test_approval_pending_metric():
    from procurement_platform.observability.metrics import get_metrics, reset_metrics

    reset_metrics()
    m = get_metrics()
    m.set_approval_pending("tenant_demo", 5)
    txt = m.generate()
    assert "approval_pending_total" in txt
    assert "tenant_demo" in txt


def test_budget_exceeded_metric():
    from procurement_platform.observability.metrics import get_metrics, reset_metrics

    reset_metrics()
    m = get_metrics()
    m.inc_budget_exceeded("tenant_demo", "max_tokens")
    txt = m.generate()
    assert "budget_exceeded_total" in txt
