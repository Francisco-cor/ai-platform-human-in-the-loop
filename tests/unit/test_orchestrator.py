from procurement_platform.domain.models import ExecutionState, NormalizedRequest
from procurement_platform.persistence.database import get_sessionmaker
from procurement_platform.workflows.orchestrator import WorkflowOrchestrator


def test_create_and_advance():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id="req_test_01",
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[{"sku": "MAT-001", "quantity": 5, "unit": "piece"}],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
    )
    exec_obj = orch.create_execution(db, normalized=norm, trace_id="trace_123")
    assert exec_obj.status == ExecutionState.RECEIVED
    exec_obj = orch.advance_synthetic(db, exec_obj.execution_id, trace_id="trace_123")
    assert exec_obj.status == ExecutionState.AWAITING_APPROVAL
    assert exec_obj.proposal is not None
    assert exec_obj.approval_request is not None
    # approve and complete
    exec_obj = orch.approve_and_complete(db, exec_obj.execution_id, decided_by="approver_01", trace_id="trace_123")
    assert exec_obj.status == ExecutionState.COMPLETED
    db.close()


def test_invalid_transition_blocked():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id="req_test_02",
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[{"sku": "MAT-001", "quantity": 5, "unit": "piece"}],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
    )
    exec_obj = orch.create_execution(db, normalized=norm)
    try:
        orch.transition(db, exec_obj.execution_id, ExecutionState.COMPLETED)
        assert False, "should have raised"
    except ValueError as e:
        assert "invalid transition" in str(e)
    db.close()
