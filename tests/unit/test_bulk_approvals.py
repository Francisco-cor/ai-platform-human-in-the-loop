"""Fase 7 — bulk decisions y CSV export tests."""

import csv
import io

from procurement_platform.domain.models import NormalizedRequest, RequestItem
from procurement_platform.workflows.orchestrator import WorkflowOrchestrator
from datetime import datetime, UTC


def test_bulk_decision_via_api(client, db_session):
    # create 3 executions
    orch = WorkflowOrchestrator()
    aids = []
    for i in range(3):
        norm = NormalizedRequest(
            request_id=f"req_bulk_{i}",
            tenant_id="tenant_demo",
            requester_id="user_01",
            items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
            horizon_days=21,
            location_id="warehouse_north",
            currency="USD",
            source="test",
            created_at=datetime.now(UTC),
        )
        exec_obj = orch.create_execution(db_session, normalized=norm, trace_id=f"trace_bulk_{i}")
        exec_obj = orch.advance_synthetic(db_session, exec_obj.execution_id, trace_id=f"trace_bulk_{i}")
        aids.append(exec_obj.approval_request.approval_id)

    # bulk approve
    resp = client.post(
        "/v1/approvals/bulk/decision",
        json={"approval_ids": aids, "decision": "approved", "decided_by": "admin_01"},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 3
    assert len(data["results"]) == 3
    for r in data["results"]:
        assert r["status"] in ("approved", "partially_approved", "error")
        # with low risk, should be approved
        assert r["status"] == "approved"

    # verify each execution completed
    for aid in aids:
        # fetch execution via approval -> get execution_id
        resp2 = client.get(f"/v1/approvals/{aid}")
        assert resp2.status_code == 200
        exec_id = resp2.json()["execution_id"]
        resp3 = client.get(f"/v1/procurement/executions/{exec_id}")
        assert resp3.json()["status"] == "COMPLETED"


def test_bulk_with_invalid_id(client, db_session):
    resp = client.post(
        "/v1/approvals/bulk/decision",
        json={"approval_ids": ["appr_notexist"], "decision": "approved", "decided_by": "admin_01"},
    )
    assert resp.status_code == 200
    assert resp.json()["results"][0]["error"] == "not_found"


def test_export_csv(client, db_session):
    # create one
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id="req_export",
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
        source="test",
        created_at=datetime.now(UTC),
    )
    exec_obj = orch.create_execution(db_session, normalized=norm, trace_id="trace_export")
    exec_obj = orch.advance_synthetic(db_session, exec_obj.execution_id, trace_id="trace_export")

    resp = client.get("/v1/approvals/export?tenant=tenant_demo&state=pending")
    assert resp.status_code == 200
    assert "text/csv" in resp.headers["content-type"]
    txt = resp.text
    assert "approval_id" in txt
    assert "tenant_demo" in txt
    # parse CSV
    reader = csv.DictReader(io.StringIO(txt))
    rows = list(reader)
    assert len(rows) >= 1
    ids = [r["approval_id"] for r in rows]
    assert exec_obj.approval_request.approval_id in ids


def test_export_filter_state(client, db_session):
    # after previous, pending should have at least 1, but after approving, pending reduces
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id="req_export2",
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
        source="test",
        created_at=datetime.now(UTC),
    )
    exec_obj = orch.create_execution(db_session, normalized=norm, trace_id="trace_export2")
    exec_obj = orch.advance_synthetic(db_session, exec_obj.execution_id, trace_id="trace_export2")
    aid = exec_obj.approval_request.approval_id
    # approve it
    orch.approve_and_complete(db_session, exec_obj.execution_id, decided_by="approver_01")
    # export pending should not contain this now-approved
    resp = client.get("/v1/approvals/export?tenant=tenant_demo&state=pending")
    txt = resp.text
    assert aid not in txt
    # export approved? our export filters by approval status, not execution status — approved is not pending, so check approved
    # but our export currently filters by approval status pending vs approved; after approve, status is approved, so not in pending
    # export all should contain
    resp2 = client.get("/v1/approvals/export?tenant=tenant_demo&state=all")
    assert aid in resp2.text


def test_list_approvals_pagination(client, db_session):
    # create 2
    orch = WorkflowOrchestrator()
    for i in range(2):
        norm = NormalizedRequest(
            request_id=f"req_list_{i}",
            tenant_id="tenant_demo",
            requester_id="user_01",
            items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
            horizon_days=21,
            location_id="warehouse_north",
            currency="USD",
            source="test",
            created_at=datetime.now(UTC),
        )
        exec_obj = orch.create_execution(db_session, normalized=norm, trace_id=f"trace_list_{i}")
        orch.advance_synthetic(db_session, exec_obj.execution_id, trace_id=f"trace_list_{i}")

    resp = client.get("/v1/approvals?tenant=tenant_demo&state=pending&limit=1")
    assert resp.status_code == 200
    data = resp.json()
    assert data["count"] == 1
    assert "approvals" in data
    assert len(data["approvals"]) == 1
    # test scope diff data present
    assert "scope_hash" in data["approvals"][0]
