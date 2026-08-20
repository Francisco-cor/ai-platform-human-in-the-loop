def test_healthz(client):
    resp = client.get("/healthz")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


def test_readyz(client):
    resp = client.get("/readyz")
    assert resp.status_code == 200
    assert resp.json()["db"] == "ok"


def test_create_execution_synthetic(client):
    payload = {
        "tenant_id": "tenant_demo",
        "requester_id": "user_01",
        "raw_intent": "Necesitamos reponer materiales críticos para las próximas tres semanas.",
        "horizon_days": 21,
        "location_id": "warehouse_north",
        "currency": "USD",
    }
    resp = client.post("/v1/procurement/executions", json=payload)
    assert resp.status_code == 202, resp.text
    data = resp.json()
    assert "execution_id" in data
    assert data["status"] == "AWAITING_APPROVAL"
    assert data["approval_request"] is not None
    execution_id = data["execution_id"]
    approval_id = data["approval_request"]["approval_id"]

    # get execution
    resp2 = client.get(f"/v1/procurement/executions/{execution_id}")
    assert resp2.status_code == 200
    assert resp2.json()[" proposal"] if False else True  # dummy
    assert resp2.json()["status"] == "AWAITING_APPROVAL"
    assert resp2.json()["proposal"] is not None

    # events
    resp3 = client.get(f"/v1/procurement/executions/{execution_id}/events")
    assert resp3.status_code == 200
    assert resp3.json()["count"] >= 1
    event_types = [e["event_type"] for e in resp3.json()["events"]]
    assert any("execution.created" in et for et in event_types)

    # idempotency
    resp4 = client.post(
        "/v1/procurement/executions",
        json=payload,
        headers={"Idempotency-Key": "test-key-123"},
    )
    assert resp4.status_code == 202
    first_id = resp4.json()["execution_id"]
    resp5 = client.post(
        "/v1/procurement/executions",
        json=payload,
        headers={"Idempotency-Key": "test-key-123"},
    )
    assert resp5.json()["execution_id"] == first_id

    # approve
    resp6 = client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "approved", "decided_by": "approver_01", "reason": "ok"},
    )
    assert resp6.status_code == 200, resp6.text
    assert resp6.json()["status"] == "approved"
    assert resp6.json()["execution_status"] == "COMPLETED"

    # verify completed
    resp7 = client.get(f"/v1/procurement/executions/{execution_id}")
    assert resp7.json()["status"] == "COMPLETED"


def test_approve_rejected(client):
    payload = {"tenant_id": "tenant_demo", "requester_id": "user_01"}
    resp = client.post("/v1/procurement/executions", json=payload)
    data = resp.json()
    approval_id = data["approval_request"]["approval_id"]
    execution_id = data["execution_id"]
    resp2 = client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "rejected", "decided_by": "approver_01"},
    )
    assert resp2.status_code == 200
    assert resp2.json()["execution_status"] == "REJECTED"
    resp3 = client.get(f"/v1/procurement/executions/{execution_id}")
    assert resp3.json()["status"] == "REJECTED"


def test_openapi_generated(client):
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    assert "openapi" in resp.json()
