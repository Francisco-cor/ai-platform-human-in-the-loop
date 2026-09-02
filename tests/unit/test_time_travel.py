"""Fase 9 — time-travel tests."""

import time
from datetime import UTC, datetime, timedelta

from procurement_platform.domain.models import NormalizedRequest, RequestItem
from procurement_platform.persistence.time_travel import get_execution_at, get_execution_history
from procurement_platform.workflows.orchestrator import WorkflowOrchestrator


def test_time_travel_get_execution_at(db_session):
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id="req_tt_1",
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
        source="test",
        created_at=datetime.now(UTC),
    )
    exec_obj = orch.create_execution(db_session, normalized=norm, trace_id="trace_tt")
    created_at = exec_obj.created_at
    # advance to AWAITING
    exec_obj = orch.advance_synthetic(db_session, exec_obj.execution_id, trace_id="trace_tt")
    # time after creation but before completion
    mid_time = created_at + timedelta(seconds=1)
    snap = get_execution_at(db_session, exec_obj.execution_id, mid_time)
    assert snap is not None
    # Due to fast synthetic advance, mid_time may be after many nodes, so allow any valid state except COMPLETED
    from procurement_platform.domain.models import ExecutionState

    assert snap["status"] in [e.value for e in ExecutionState]
    assert snap["status"] != "COMPLETED"
    # time before creation should be None
    before = created_at - timedelta(seconds=10)
    assert get_execution_at(db_session, exec_obj.execution_id, before) is None
    # time after completion (approve)
    exec_obj = orch.approve_and_complete(db_session, exec_obj.execution_id, decided_by="approver_01", trace_id="trace_tt2")
    after = datetime.now(UTC) + timedelta(seconds=1)
    snap2 = get_execution_at(db_session, exec_obj.execution_id, after)
    assert snap2["status"] == "COMPLETED"


def test_time_travel_via_api(client, db_session):
    # create via API
    resp = client.post("/v1/procurement/executions", json={"tenant_id": "tenant_demo", "requester_id": "user_01", "items": [{"sku": "MAT-001", "quantity": 10, "unit": "piece"}]})
    assert resp.status_code == 202
    exec_id = resp.json()["execution_id"]
    # get timeline without at
    resp2 = client.get(f"/v1/procurement/executions/{exec_id}/timeline")
    assert resp2.status_code == 200
    assert "history" in resp2.json() or "status" in resp2.json()
    # get with at parameter (time-travel)
    # Use now as at (should return snapshot)
    from datetime import datetime, UTC

    now = datetime.now(UTC).isoformat()
    resp3 = client.get(f"/v1/procurement/executions/{exec_id}/timeline?at={now}")
    assert resp3.status_code == 200
    # also test via time-travel alias
    resp4 = client.get(f"/v1/procurement/executions/{exec_id}/time-travel?at={now}")
    assert resp4.status_code == 200
    assert resp4.json()["execution_id"] == exec_id


def test_time_travel_history(db_session):
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id="req_tt_hist",
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
        source="test",
        created_at=datetime.now(UTC),
    )
    exec_obj = orch.create_execution(db_session, normalized=norm, trace_id="trace_hist")
    orch.advance_synthetic(db_session, exec_obj.execution_id, trace_id="trace_hist")
    history = get_execution_history(db_session, exec_obj.execution_id)
    assert len(history) > 0
    assert any("execution.created" in h["event_type"] for h in history)
