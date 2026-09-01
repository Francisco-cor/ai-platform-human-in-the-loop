"""Contract tests for pagination and payload streaming — F1-4."""

import pytest
from fastapi.testclient import TestClient


def test_events_pagination_stable(client: TestClient):
    # create execution
    resp = client.post(
        "/v1/procurement/executions",
        json={"tenant_id": "tenant_demo", "requester_id": "user_01", "raw_intent": "test pagination"},
    )
    assert resp.status_code == 202
    exec_id = resp.json()["execution_id"]

    # fetch events with limit 2 — should return stable order and has_more
    r1 = client.get(f"/v1/procurement/executions/{exec_id}/events?limit=2")
    assert r1.status_code == 200
    data1 = r1.json()
    assert "events" in data1
    assert "total" in data1
    assert "has_more" in data1
    assert "next_cursor" in data1
    assert data1["limit"] == 2
    assert data1["count"] <= 2
    # if has_more, next_cursor should be set
    if data1["has_more"]:
        assert data1["next_cursor"] is not None
        # fetch next page with cursor
        r2 = client.get(f"/v1/procurement/executions/{exec_id}/events?limit=2&cursor={data1['next_cursor']}")
        assert r2.status_code == 200
        data2 = r2.json()
        # events should not overlap (stable)
        ids1 = {e["event_id"] for e in data1["events"]}
        ids2 = {e["event_id"] for e in data2["events"]}
        assert ids1.isdisjoint(ids2)
    # limit clamp: >100 should be clamped to 100
    r3 = client.get(f"/v1/procurement/executions/{exec_id}/events?limit=999")
    assert r3.status_code == 200
    assert r3.json()["limit"] == 100
    # limit <1 should be clamped to 1
    r4 = client.get(f"/v1/procurement/executions/{exec_id}/events?limit=0")
    assert r4.status_code == 200
    assert r4.json()["limit"] == 1


def test_payload_streaming_limit(client: TestClient):
    # craft payload larger than 256KB (max_payload_bytes)
    large_content = "x" * (300 * 1024)  # 300KB
    resp = client.post(
        "/v1/documents",
        json={
            "tenant_id": "tenant_demo",
            "title": "large",
            "content": large_content,
            "doc_type": "policy",
        },
    )
    # should be rejected 413 or 422 (depending on validation) but not 200
    assert resp.status_code in (413, 422)
    if resp.status_code == 413:
        assert resp.json()["code"] == "payload_too_large"
