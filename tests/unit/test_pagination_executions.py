"""Fase 8 — pagination stable for executions list."""

from fastapi.testclient import TestClient


def test_executions_pagination_stable(client: TestClient, db_session):
    # clean via creating multiple executions with same tenant
    # create 5 executions
    ids = []
    for i in range(5):
        resp = client.post(
            "/v1/procurement/executions",
            json={
                "tenant_id": "tenant_demo",
                "requester_id": "user_01",
                "items": [{"sku": "MAT-001", "quantity": 10, "unit": "piece"}],
                "horizon_days": 21,
            },
        )
        assert resp.status_code == 202
        ids.append(resp.json()["execution_id"])

    # list with limit 2
    resp = client.get("/v1/procurement/executions?tenant=tenant_demo&state=all&limit=2")
    assert resp.status_code == 200
    data = resp.json()
    assert "executions" in data
    assert "total_count" in data
    assert "has_more" in data
    assert "page_size" in data
    assert "next_cursor" in data
    assert data["page_size"] == 2
    assert data["count"] == 2
    assert data["total_count"] >= 5
    assert data["has_more"] is True
    assert data["next_cursor"] is not None

    # second page via cursor should not overlap
    next_cursor = data["next_cursor"]
    resp2 = client.get(f"/v1/procurement/executions?tenant=tenant_demo&state=all&limit=2&cursor={next_cursor}")
    assert resp2.status_code == 200
    data2 = resp2.json()
    ids1 = {e["execution_id"] for e in data["executions"]}
    ids2 = {e["execution_id"] for e in data2["executions"]}
    assert ids1.isdisjoint(ids2)

    # stable order: should be created_at asc, execution_id asc
    # fetch all with limit 100 and check order
    resp_all = client.get("/v1/procurement/executions?tenant=tenant_demo&state=all&limit=100")
    all_execs = resp_all.json()["executions"]
    # check that created_at is ascending
    ctimes = [e["created_at"] for e in all_execs]
    assert ctimes == sorted(ctimes)

    # limit clamp
    resp_big = client.get("/v1/procurement/executions?tenant=tenant_demo&limit=999")
    assert resp_big.json()["page_size"] == 100

    resp_small = client.get("/v1/procurement/executions?tenant=tenant_demo&limit=0")
    assert resp_small.json()["page_size"] == 1

    # tenant filter
    resp_tenant = client.get("/v1/procurement/executions?tenant=tenant_other&limit=10")
    # should be 0 or filtered
    assert resp_tenant.status_code == 200
    for e in resp_tenant.json()["executions"]:
        assert e["tenant_id"] == "tenant_other"
