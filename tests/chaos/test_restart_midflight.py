"""Chaos: restart midflight and duplicate detection — F2-6."""

import threading

from procurement_platform.domain.models import ExecutionState, NormalizedRequest, RequestItem, new_id, utcnow
from procurement_platform.workflows.orchestrator import WorkflowOrchestrator


def test_restart_midflight_awaiting_no_duplicate(db_session):
    """Simula kill tras AWAITING y resume — no debe duplicar orden."""
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
        raw_intent="chaos restart awaiting",
    )
    exec_obj = orch.create_execution(db_session, normalized=norm, trace_id="chaos1", actor_id="user_01")
    exec_id = exec_obj.execution_id
    exec_obj = orch.advance_synthetic(db_session, exec_id, trace_id="chaos1")
    assert exec_obj.status == ExecutionState.AWAITING_APPROVAL
    # simulate restart: new orchestrator instance (same DB) calls resume before approval — should stay AWAITING
    new_orch = WorkflowOrchestrator()
    resumed = new_orch.resume_durable(db_session, exec_id, trace_id="chaos1-resume")
    assert resumed.status == ExecutionState.AWAITING_APPROVAL
    # approve now
    exec_obj = new_orch.approve_and_complete(db_session, exec_id, decided_by="approver_01", trace_id="chaos1")
    assert exec_obj.status == ExecutionState.COMPLETED
    # simulate second resume after completed — idempotent, no duplicate
    resumed2 = new_orch.resume_durable(db_session, exec_id, trace_id="chaos1-resume2")
    assert resumed2.status == ExecutionState.COMPLETED
    from procurement_platform.tools.gateway import _GLOBAL_CALL_LOG

    submits = [c for c in _GLOBAL_CALL_LOG if c["execution_id"] == exec_id and c["tool"] == "submit_purchase_order"]
    assert len(submits) == 1


def test_restart_partial_early_state_no_loss(db_session):
    """Kill en early state (RECEIVED) y resume debe avanzar sin perder datos."""
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
        raw_intent="chaos early",
    )
    exec_obj = orch.create_execution(db_session, normalized=norm, trace_id="chaos2", actor_id="user_01")
    exec_id = exec_obj.execution_id
    # don't call advance_synthetic — stay RECEIVED
    assert exec_obj.status == ExecutionState.RECEIVED
    # resume should advance to AWAITING
    resumed = orch.resume_durable(db_session, exec_id, trace_id="chaos2-resume")
    assert resumed.status in (ExecutionState.AWAITING_APPROVAL, ExecutionState.BLOCKED, ExecutionState.COMPLETED)


def test_concurrent_resume_only_one_wins(db_session):
    """Dos resumes concurrentes — solo uno debe tomar lock, otro raises conflict."""
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
        raw_intent="chaos concurrent",
    )
    exec_obj = orch.create_execution(db_session, normalized=norm, trace_id="chaos3", actor_id="user_01")
    exec_id = exec_obj.execution_id
    # stay at RECEIVED, then concurrent resume attempts
    results = []

    def try_resume():
        try:
            r = orch.resume_durable(db_session, exec_id, trace_id="chaos3")
            results.append(("ok", r.status.value))
        except Exception as e:
            results.append(("err", str(e)))

    # use threads but db_session is not thread-safe for sqlite — simulate lock contention via LockManager directly
    from procurement_platform.infra.locks.manager import get_lock_manager

    mgr = get_lock_manager()
    # acquire lock manually to simulate concurrent resume holding it
    assert mgr.acquire("orchestrator:" + exec_id, blocking=False) is True
    try:
        try:
            orch.resume_durable(db_session, exec_id, trace_id="chaos3")
            results.append(("unexpected_ok", ""))
        except ValueError as e:
            assert "locked" in str(e)
            results.append(("locked", str(e)))
    finally:
        mgr.release("orchestrator:" + exec_id)

    assert any(r[0] == "locked" for r in results)
