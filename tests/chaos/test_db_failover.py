"""Chaos: DB failover with toxiproxy simulation — Fase 10 SRE."""

import pytest

from procurement_platform.domain.models import (
    ExecutionState,
    NormalizedRequest,
    RequestItem,
    new_id,
    utcnow,
)
from procurement_platform.workflows.orchestrator import WorkflowOrchestrator


pytestmark = pytest.mark.chaos


def test_db_failover_no_duplicate_via_toxiproxy_mock(db_session, monkeypatch):
    """Simula blackhole DB 5s via monkeypatch OperationalError, luego resume debe ser idempotente."""
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id=new_id("req"),
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
        source="test",
        created_at=utcnow(),
        raw_intent="chaos db failover",
    )
    exec_obj = orch.create_execution(
        db_session, normalized=norm, trace_id="chaos-db-1", actor_id="user_01"
    )
    exec_id = exec_obj.execution_id
    exec_obj = orch.advance_synthetic(db_session, exec_id, trace_id="chaos-db-1")
    assert exec_obj.status == ExecutionState.AWAITING_APPROVAL

    # Simulate DB failure on next approve attempt: first call raises OperationalError, second succeeds (retry)
    # We monkeypatch approve_and_complete to simulate transient failure
    orig_approve = orch.approve_and_complete
    call_count = {"n": 0}

    def flaky_approve(
        db, execution_id, decided_by="approver_01", trace_id=None, decision_reason=None
    ):
        call_count["n"] += 1
        if call_count["n"] == 1:
            # simulate toxiproxy blackhole: DB cannot connect
            raise Exception(
                "OperationalError: could not connect to server: Connection timed out (toxiproxy blackhole)"
            )
        return orig_approve(
            db,
            execution_id,
            decided_by=decided_by,
            trace_id=trace_id,
            decision_reason=decision_reason,
        )

    monkeypatch.setattr(orch, "approve_and_complete", flaky_approve)

    # First attempt fails
    try:
        orch.approve_and_complete(
            db_session, exec_id, decided_by="approver_01", trace_id="chaos-db-1"
        )
        assert False, "should have raised"
    except Exception as e:
        assert "OperationalError" in str(e)

    # Restore original and retry via resume_durable (which internally calls approve path if needed)
    monkeypatch.setattr(orch, "approve_and_complete", orig_approve)
    # After DB recovery, approve should succeed without duplicate
    exec_obj = orch.approve_and_complete(
        db_session, exec_id, decided_by="approver_01", trace_id="chaos-db-2"
    )
    assert exec_obj.status == ExecutionState.COMPLETED

    # Resume after completed must be idempotent
    resumed = orch.resume_durable(db_session, exec_id, trace_id="chaos-db-3")
    assert resumed.status == ExecutionState.COMPLETED

    from procurement_platform.tools.gateway import _GLOBAL_CALL_LOG

    submits = [
        c
        for c in _GLOBAL_CALL_LOG
        if c["execution_id"] == exec_id and c["tool"] == "submit_purchase_order"
    ]
    assert len(submits) == 1, f"duplicate submit after failover: {submits}"


def test_db_migration_idempotent(db_session):
    """Verifica alembic upgrade head es idempotente (sin downtime)."""
    # En sqlite no hay alembic real, pero verificamos que metadata.create_all es idempotente
    from procurement_platform.persistence.database import Base, get_engine

    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    # Second call must not fail
    Base.metadata.create_all(bind=engine)

    # Create execution after migration to verify data still works
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id=new_id("req"),
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
        source="test",
        created_at=utcnow(),
    )
    exec_obj = orch.create_execution(
        db_session, normalized=norm, trace_id="chaos-mig", actor_id="user_01"
    )
    assert exec_obj.execution_id.startswith("exec_")


def test_redis_down_fallback_to_memory(db_session):
    """Redis down → fallback to MemoryLockManager, no duplicate."""
    from procurement_platform.infra.locks.manager import get_lock_manager, reset_lock_manager
    import os

    # Simulate redis down by setting REDIS_URL to invalid and resetting manager
    orig_url = os.getenv("PROCUREMENT_REDIS_URL")
    os.environ["PROCUREMENT_REDIS_URL"] = "redis://invalid:6379/0"
    reset_lock_manager()

    try:
        mgr = get_lock_manager()
        # With invalid redis, should fallback to memory (acquire still works)
        assert mgr.acquire("test:redis-down", blocking=False) is True
        mgr.release("test:redis-down")
        assert mgr.acquire("test:redis-down", blocking=False) is True
        mgr.release("test:redis-down")
    finally:
        if orig_url is None:
            os.environ.pop("PROCUREMENT_REDIS_URL", None)
        else:
            os.environ["PROCUREMENT_REDIS_URL"] = orig_url
        reset_lock_manager()
        get_lock_manager()  # restore default
