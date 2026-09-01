"""F5-3 structured audit trace drill-down tests."""

from fastapi.testclient import TestClient


def test_events_include_trace_and_model_metadata(client):
    # create execution
    resp = client.post(
        "/v1/procurement/executions",
        json={
            "tenant_id": "tenant_demo",
            "requester_id": "user_01",
            "raw_intent": "Necesitamos reponer materiales críticos para las próximas tres semanas.",
            "horizon_days": 21,
            "location_id": "warehouse_north",
        },
    )
    assert resp.status_code == 202
    exec_id = resp.json()["execution_id"]
    # get events normal
    resp2 = client.get(f"/v1/procurement/executions/{exec_id}/events", params={"limit": 50})
    assert resp2.status_code == 200
    data = resp2.json()
    assert "events" in data
    for ev in data["events"]:
        assert "trace_id" in ev
        assert "model_metadata" in ev or "details" in ev
        # model_metadata should contain prompt_version and graph_version
        if ev.get("model_metadata"):
            assert "prompt_version" in ev["model_metadata"]
            assert "graph_version" in ev["model_metadata"]


def test_events_format_trace(client):
    resp = client.post(
        "/v1/procurement/executions",
        json={
            "tenant_id": "tenant_demo",
            "requester_id": "user_01",
            "raw_intent": "Necesitamos reponer materiales para trace test",
        },
    )
    assert resp.status_code == 202
    exec_id = resp.json()["execution_id"]
    # get trace format
    resp2 = client.get(
        f"/v1/procurement/executions/{exec_id}/events", params={"limit": 50, "format": "trace"}
    )
    assert resp2.status_code == 200
    data = resp2.json()
    assert "timeline" in data
    assert "trace_id" in data
    for item in data["timeline"]:
        assert "event_type" in item
        assert "timestamp" in item
        assert "trace_id" in item
        # span_id and duration_ms may be in details
        assert "model_metadata" in item


def test_execution_has_trace_id(client):
    resp = client.post(
        "/v1/procurement/executions",
        json={
            "tenant_id": "tenant_demo",
            "requester_id": "user_01",
            "raw_intent": "trace test for execution",
        },
    )
    assert resp.status_code == 202
    exec_id = resp.json()["execution_id"]
    resp2 = client.get(f"/v1/procurement/executions/{exec_id}")
    assert resp2.status_code == 200
    assert resp2.json()["trace_id"] is not None
    assert len(resp2.json()["trace_id"]) >= 16
