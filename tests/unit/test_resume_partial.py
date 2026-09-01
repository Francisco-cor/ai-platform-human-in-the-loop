"""Test resume partial from checkpoint — F2-3."""

from procurement_platform.domain.models import (
    ExecutionState,
    NormalizedRequest,
    RequestItem,
    new_id,
    utcnow,
)
from procurement_platform.workflows.orchestrator import WorkflowOrchestrator


def test_resume_from_received_advances_to_awaiting(db_session):
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id=new_id("req"),
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
        source="agent_station",
        created_at=utcnow(),
        raw_intent="test resume partial",
    )
    exec_obj = orch.create_execution(
        db_session, normalized=norm, trace_id="trace_resume", actor_id="user_01"
    )
    exec_id = exec_obj.execution_id
    # simulate early state: keep RECEIVED (not yet advanced)
    # call resume_durable should advance via advance_synthetic to AWAITING
    resumed = orch.resume_durable(db_session, exec_id, trace_id="trace_resume2")
    assert resumed.status in (
        ExecutionState.AWAITING_APPROVAL,
        ExecutionState.BLOCKED,
        ExecutionState.COMPLETED,
    )
    # should have audit execution.resume.attempt
    from procurement_platform.persistence.models import AuditEventRow

    events = db_session.query(AuditEventRow).filter(AuditEventRow.execution_id == exec_id).all()
    assert any(e.event_type == "execution.resume.attempt" for e in events)


def test_resume_idempotent_on_completed(db_session):
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id=new_id("req"),
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
        source="agent_station",
        created_at=utcnow(),
        raw_intent="test resume idempotent completed",
    )
    exec_obj = orch.create_execution(
        db_session, normalized=norm, trace_id="trace_resume", actor_id="user_01"
    )
    exec_id = exec_obj.execution_id
    # advance to awaiting
    exec_obj = orch.advance_synthetic(db_session, exec_id, trace_id="trace_resume")
    # approve to completed
    exec_obj = orch.approve_and_complete(
        db_session, exec_id, decided_by="approver_01", trace_id="trace_resume"
    )
    assert exec_obj.status == ExecutionState.COMPLETED
    # resume again should be idempotent no duplicate
    resumed = orch.resume_durable(db_session, exec_id, trace_id="trace_resume2")
    assert resumed.status == ExecutionState.COMPLETED
    # check only one submit
    from procurement_platform.tools.gateway import _GLOBAL_CALL_LOG

    submits = [
        c
        for c in _GLOBAL_CALL_LOG
        if c["execution_id"] == exec_id and c["tool"] == "submit_purchase_order"
    ]
    assert len(submits) == 1
