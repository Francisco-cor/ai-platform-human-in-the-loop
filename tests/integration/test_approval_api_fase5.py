"""API tests Fase 5 — snapshot, expiración, scope_mismatch, idempotencia y reanudación."""

from datetime import timedelta

from procurement_platform.domain.models import utcnow
from procurement_platform.persistence.database import get_sessionmaker
from procurement_platform.persistence.models import WorkflowExecution


def test_api_approval_snapshot_present(client):
    resp = client.post(
        "/v1/procurement/executions",
        json={
            "tenant_id": "tenant_demo",
            "requester_id": "user_01",
            "items": [{"sku": "MAT-001", "quantity": 15, "unit": "piece"}],
        },
    )
    assert resp.status_code == 202
    data = resp.json()
    approval_id = data["approval_request"]["approval_id"]
    execution_id = data["execution_id"]
    # GET approval
    r2 = client.get(f"/v1/approvals/{approval_id}")
    assert r2.status_code == 200
    appr = r2.json()
    assert appr["approval_id"] == approval_id
    assert appr["proposal_snapshot"] is not None
    assert appr["scope_hash"] == appr["proposal_snapshot"]["scope_hash"]
    assert appr["risk_level"] is not None
    assert appr["required_approvals"] >= 1
    # GET execution still awaiting
    r3 = client.get(f"/v1/procurement/executions/{execution_id}")
    assert r3.json()["status"] == "AWAITING_APPROVAL"


def test_api_expired_approval_blocks(client):
    resp = client.post(
        "/v1/procurement/executions", json={"tenant_id": "tenant_demo", "requester_id": "user_01"}
    )
    assert resp.status_code == 202
    approval_id = resp.json()["approval_request"]["approval_id"]
    execution_id = resp.json()["execution_id"]
    # manually expire via DB — need to flag JSON modification
    from sqlalchemy.orm.attributes import flag_modified

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    row = db.get(WorkflowExecution, execution_id)
    appr = dict(row.approval_request)  # copy
    past = (utcnow() - timedelta(hours=2)).isoformat()
    appr["expires_at"] = past
    row.approval_request = appr
    flag_modified(row, "approval_request")
    db.commit()
    db.close()
    # now approving should 409 expired
    r2 = client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "approved", "decided_by": "approver_01"},
    )
    assert r2.status_code == 409
    assert r2.json()["code"] == "expired"
    # GET should show EXPIRED
    r3 = client.get(f"/v1/procurement/executions/{execution_id}")
    assert r3.json()["status"] == "EXPIRED"
    r4 = client.get(f"/v1/approvals/{approval_id}")
    assert r4.json()["status"] == "expired"


def test_api_scope_mismatch(client):
    resp = client.post(
        "/v1/procurement/executions",
        json={
            "tenant_id": "tenant_demo",
            "requester_id": "user_01",
            "items": [{"sku": "MAT-001", "quantity": 10, "unit": "piece"}],
        },
    )
    approval_id = resp.json()["approval_request"]["approval_id"]
    execution_id = resp.json()["execution_id"]
    # tamper proposal
    from sqlalchemy.orm.attributes import flag_modified

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    row = db.get(WorkflowExecution, execution_id)
    prop = dict(row.proposal)
    prop["supplier_id"] = "tampered_supplier"
    prop["scope_hash"] = "sha256:tampered"
    row.proposal = prop
    flag_modified(row, "proposal")
    db.commit()
    db.close()
    r2 = client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "approved", "decided_by": "approver_01"},
    )
    assert r2.status_code == 409
    assert r2.json()["code"] == "scope_mismatch"
    # with explicit scope_hash mismatch from client
    r3 = client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "approved", "decided_by": "approver_01", "scope_hash": "sha256:wrong"},
    )
    assert r3.status_code == 409
    assert r3.json()["code"] == "scope_mismatch"


def test_api_idempotent_approval_with_key(client):
    resp = client.post(
        "/v1/procurement/executions",
        json={
            "tenant_id": "tenant_demo",
            "requester_id": "user_01",
            "items": [{"sku": "MAT-001", "quantity": 10, "unit": "piece"}],
        },
    )
    approval_id = resp.json()["approval_request"]["approval_id"]
    execution_id = resp.json()["execution_id"]
    # first approve with idempotency key
    r1 = client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "approved", "decided_by": "approver_01"},
        headers={"Idempotency-Key": "idem-test-789"},
    )
    assert r1.status_code == 200
    assert r1.json()["execution_status"] == "COMPLETED"
    # second with same key but different decided_by should still return first response (idempotent)
    r2 = client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "approved", "decided_by": "approver_99"},
        headers={"Idempotency-Key": "idem-test-789"},
    )
    assert r2.status_code == 200
    assert r2.json()["execution_status"] == "COMPLETED"
    # verify only one order via events? check events contain only one submit
    ev = client.get(f"/v1/procurement/executions/{execution_id}/events").json()
    submits = [e for e in ev["events"] if "tool.submit_purchase_order" in e["event_type"]]
    # should be exactly 1 (gateway idempotency)
    assert len(submits) == 1
    # GET execution still completed idempotent
    r3 = client.get(f"/v1/procurement/executions/{execution_id}")
    assert r3.json()["status"] == "COMPLETED"


def test_api_never_execute_without_approval(client):
    # create execution
    resp = client.post(
        "/v1/procurement/executions", json={"tenant_id": "tenant_demo", "requester_id": "user_01"}
    )
    execution_id = resp.json()["execution_id"]
    # try to directly call resume without approval — should stay awaiting, not completed
    r2 = client.post(f"/v1/procurement/executions/{execution_id}/resume")
    assert r2.status_code == 200
    assert r2.json()["status"] == "AWAITING_APPROVAL"
    # verify no purchase order executed
    r3 = client.get(f"/v1/procurement/executions/{execution_id}/events").json()
    exec_events = [e["event_type"] for e in r3["events"]]
    assert not any("tool.submit_purchase_order" in e for e in exec_events)


def test_api_reject_and_needs_changes(client):
    # reject
    resp = client.post(
        "/v1/procurement/executions", json={"tenant_id": "tenant_demo", "requester_id": "user_01"}
    )
    approval_id = resp.json()["approval_request"]["approval_id"]
    execution_id = resp.json()["execution_id"]
    r2 = client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "rejected", "decided_by": "approver_01", "reason": "no budget"},
    )
    assert r2.status_code == 200
    assert r2.json()["execution_status"] == "REJECTED"
    r3 = client.get(f"/v1/procurement/executions/{execution_id}")
    assert r3.json()["status"] == "REJECTED"
    # cannot approve after rejected
    r4 = client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "approved", "decided_by": "approver_02"},
    )
    assert r4.status_code == 409

    # needs_changes
    resp2 = client.post(
        "/v1/procurement/executions", json={"tenant_id": "tenant_demo", "requester_id": "user_01"}
    )
    approval_id2 = resp2.json()["approval_request"]["approval_id"]
    execution_id2 = resp2.json()["execution_id"]
    r5 = client.post(
        f"/v1/approvals/{approval_id2}/decision",
        json={"decision": "needs_changes", "decided_by": "approver_01", "reason": "falta info"},
    )
    assert r5.status_code == 200
    assert r5.json()["execution_status"] == "NEEDS_CLARIFICATION"


def test_api_resume_idempotent(client):
    resp = client.post(
        "/v1/procurement/executions", json={"tenant_id": "tenant_demo", "requester_id": "user_01"}
    )
    approval_id = resp.json()["approval_request"]["approval_id"]
    execution_id = resp.json()["execution_id"]
    # approve
    r2 = client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "approved", "decided_by": "approver_01"},
    )
    assert r2.json()["execution_status"] == "COMPLETED"
    # resume again idempotent
    r3 = client.post(f"/v1/procurement/executions/{execution_id}/resume")
    assert r3.status_code == 200
    assert r3.json()["status"] == "COMPLETED"
    # second approve same approver is idempotent 200 (no duplicate order)
    r4 = client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "approved", "decided_by": "approver_01"},
    )
    assert r4.status_code == 200
    assert r4.json()["execution_status"] == "COMPLETED"
    # second approve with different approver after completed should be 409 already_decided
    r5 = client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "approved", "decided_by": "approver_02"},
    )
    assert r5.status_code == 409


def test_api_double_approval_high_risk(client):
    # Force high risk by using missing supplier scenario via DB tampering: set risk high and required 2
    from sqlalchemy.orm.attributes import flag_modified

    resp = client.post(
        "/v1/procurement/executions",
        json={
            "tenant_id": "tenant_demo",
            "requester_id": "user_01",
            "items": [{"sku": "MAT-001", "quantity": 10, "unit": "piece"}],
        },
    )
    approval_id = resp.json()["approval_request"]["approval_id"]
    execution_id = resp.json()["execution_id"]
    # patch approval to require 2 and risk high
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    row = db.get(WorkflowExecution, execution_id)
    appr = dict(row.approval_request)
    appr["risk_level"] = "high"
    appr["required_approvals"] = 2
    row.approval_request = appr
    flag_modified(row, "approval_request")
    # also proposal risk high
    prop = dict(row.proposal)
    prop["risk_level"] = "high"
    row.proposal = prop
    flag_modified(row, "proposal")
    db.commit()
    db.close()
    # first approval partially
    r1 = client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "approved", "decided_by": "approver_01"},
    )
    assert r1.status_code == 200
    assert r1.json()["status"] == "partially_approved"
    assert r1.json()["execution_status"] == "AWAITING_APPROVAL"
    # still awaiting
    r2 = client.get(f"/v1/procurement/executions/{execution_id}")
    assert r2.json()["status"] == "AWAITING_APPROVAL"
    # second approval completes
    r3 = client.post(
        f"/v1/approvals/{approval_id}/decision",
        json={"decision": "approved", "decided_by": "approver_02"},
    )
    assert r3.status_code == 200
    assert r3.json()["execution_status"] == "COMPLETED"
    r4 = client.get(f"/v1/procurement/executions/{execution_id}")
    assert r4.json()["status"] == "COMPLETED"
