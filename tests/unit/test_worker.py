"""Worker skeleton tests — F2-2."""

from unittest.mock import patch

from procurement_platform.workers.tasks import enqueue_workflow, run_workflow_sync


def test_enqueue_fallback_when_async_disabled():
    # async disabled by default in ci
    ok = enqueue_workflow("exec_test", trace_id="trace_123")
    assert ok is False  # fallback to sync


def test_enqueue_when_async_enabled_but_no_redis(monkeypatch):
    monkeypatch.setenv("PROCUREMENT_ASYNC_ENABLED", "true")
    from procurement_platform.config.settings import reset_settings_cache

    reset_settings_cache()
    # ensure redis url points to localhost (no server) — should fallback
    monkeypatch.setenv("PROCUREMENT_REDIS_URL", "redis://localhost:6379/1")
    reset_settings_cache()
    ok = enqueue_workflow("exec_test2", trace_id="trace_456")
    # without redis server, should return False (fallback)
    assert ok is False
    # reset
    monkeypatch.setenv("PROCUREMENT_ASYNC_ENABLED", "false")
    monkeypatch.setenv("PROCUREMENT_REDIS_URL", "redis://localhost:6379/1")
    reset_settings_cache()


def test_run_workflow_sync_creates_execution(db_session):
    from procurement_platform.domain.models import NormalizedRequest, RequestItem, utcnow, new_id
    from procurement_platform.workflows.orchestrator import WorkflowOrchestrator

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
        raw_intent="test worker sync",
    )
    exec_obj = orch.create_execution(db_session, normalized=norm, trace_id="trace_test", actor_id="user_01")
    exec_id = exec_obj.execution_id
    # run via sync helper (advances until AWAITING)
    from procurement_platform.workers.tasks import run_workflow_sync

    # run_workflow_sync uses new session, so we need to allow it to find execution
    # it will create its own session; ensure execution exists in DB (commit already done in create_execution)
    result = run_workflow_sync(exec_id, trace_id="trace_test")
    assert result["execution_id"] == exec_id
    # verify after run, execution reached AWAITING or BLOCKED
    refreshed = orch.get_execution(db_session, exec_id)
    assert refreshed.status.value in ("AWAITING_APPROVAL", "BLOCKED", "COMPLETED")
