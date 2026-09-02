"""Fase 7 — notifications service tests."""

import os

from procurement_platform.notifications.service import get_notifier, reset_notifier, get_notification_log
from procurement_platform.workflows.orchestrator import WorkflowOrchestrator, reset_finops_state
from procurement_platform.domain.models import NormalizedRequest, RequestItem
from datetime import datetime, UTC


def test_notification_on_approval_requested(db_session):
    reset_notifier()
    reset_finops_state()
    # ensure notifications enabled for test (via in-memory log, even if disabled we still log)
    notifier = get_notifier()
    notifier.clear_log()
    # create execution via orchestrator (which triggers notification)
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id="req_notif_test",
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
        source="test",
        created_at=datetime.now(UTC),
    )
    exec_obj = orch.create_execution(db_session, normalized=norm, trace_id="trace_notif", actor_id="user_01")
    exec_obj = orch.advance_synthetic(db_session, exec_obj.execution_id, trace_id="trace_notif")
    assert exec_obj.status.value == "AWAITING_APPROVAL"
    # check notification log
    log = get_notification_log()
    # should have at least one approval.requested notification
    assert len(log) >= 1
    entry = log[-1]
    assert entry["type"] == "approval.requested"
    payload = entry["payload"]
    assert payload["approval_id"] == exec_obj.approval_request.approval_id
    assert "scope_hash" in payload
    assert payload["scope_hash_trunc"] in payload["scope_hash"] or len(payload["scope_hash_trunc"]) <= 20
    assert "link" in payload and "/approvals/" in payload["link"]
    # check audit notification.sent
    from procurement_platform.persistence.models import AuditEventRow

    rows = db_session.query(AuditEventRow).filter(AuditEventRow.execution_id == exec_obj.execution_id).all()
    event_types = [r.event_type for r in rows]
    assert "approval.requested" in event_types
    assert "notification.sent" in event_types
    reset_notifier()


def test_notifier_channels():
    reset_notifier()
    notifier = get_notifier()
    # by default disabled, but channels still return skipped
    res = notifier.notify_approval_requested(
        approval_id="appr_test",
        execution_id="exec_test",
        request_id="req_test",
        tenant_id="tenant_demo",
        total=100,
        currency="USD",
        risk_level="low",
        scope_hash="sha256:abc123def456",
        trace_id="trace_test",
    )
    assert len(res) == 3
    for r in res:
        assert r.channel in ("email", "slack", "webhook")
        # when disabled, should be skipped but success
        assert r.success
    # log should have entry
    log = get_notification_log()
    assert any(e["payload"]["approval_id"] == "appr_test" for e in log)
    reset_notifier()


def test_notification_via_api(client, db_session):
    # via API, notification should be triggered
    reset_notifier()
    from procurement_platform.notifications.service import get_notification_log

    # clear log
    get_notifier().clear_log()
    resp = client.post(
        "/v1/procurement/executions",
        json={"tenant_id": "tenant_demo", "requester_id": "user_01", "items": [{"sku": "MAT-001", "quantity": 10, "unit": "piece"}]},
    )
    assert resp.status_code == 202
    # check log via direct (since API uses same process)
    log = get_notification_log()
    # at least one notification from this execution
    assert len(log) >= 1
    # check that notification has trace_id from request
    # find last
    last = log[-1]
    assert "approval_id" in last["payload"]
    reset_notifier()
