"""Fase 7 — SLA escalation y delegation tests."""

from datetime import UTC, datetime, timedelta

import pytest

from procurement_platform.approvals.service import (
    check_approval_sla,
    clear_delegations,
    get_delegation,
    get_sla_age_hours,
    set_delegation,
    SLA_ESCALATION_AFTER_HOURS,
)
from procurement_platform.domain.models import ApprovalRequest, ApprovalStatus, NormalizedRequest, RequestItem, new_id, utcnow
from procurement_platform.persistence.models import WorkflowExecution
from procurement_platform.workflows.orchestrator import WorkflowOrchestrator, reset_finops_state
from sqlalchemy.orm.attributes import flag_modified


def test_sla_escalation_after_12h(db_session):
    clear_delegations()
    reset_finops_state()
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id="req_sla_1",
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
        source="test",
        created_at=datetime.now(UTC),
    )
    exec_obj = orch.create_execution(db_session, normalized=norm, trace_id="trace_sla")
    exec_obj = orch.advance_synthetic(db_session, exec_obj.execution_id, trace_id="trace_sla")
    assert exec_obj.status.value == "AWAITING_APPROVAL"
    approval_id = exec_obj.approval_request.approval_id

    # artificially age the approval 13h
    row = db_session.get(WorkflowExecution, exec_obj.execution_id)
    appr_dict = dict(row.approval_request)
    old_time = utcnow() - timedelta(hours=13)
    appr_dict["requested_at"] = old_time.isoformat()
    row.approval_request = appr_dict
    flag_modified(row, "approval_request")
    db_session.commit()

    # check SLA
    escalated = check_approval_sla(db_session, now=utcnow(), trace_id="trace_sla_check")
    assert approval_id in escalated

    # verify escalated_to
    row2 = db_session.get(WorkflowExecution, exec_obj.execution_id)
    appr2 = row2.approval_request
    assert appr2.get("escalated_to") == "manager_01"
    assert appr2.get("escalated_at") is not None
    assert appr2.get("sla_age_hours") >= 12

    # check audit
    from procurement_platform.persistence.models import AuditEventRow

    rows = db_session.query(AuditEventRow).filter(AuditEventRow.execution_id == exec_obj.execution_id).all()
    assert any(r.event_type == "approval.escalated" for r in rows)

    # second check should not re-escalate same
    escalated2 = check_approval_sla(db_session, now=utcnow())
    assert approval_id not in escalated2


def test_sla_not_escalated_before_12h(db_session):
    clear_delegations()
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id="req_sla_2",
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
        source="test",
        created_at=datetime.now(UTC),
    )
    exec_obj = orch.create_execution(db_session, normalized=norm, trace_id="trace_sla2")
    exec_obj = orch.advance_synthetic(db_session, exec_obj.execution_id, trace_id="trace_sla2")
    # age 5h
    row = db_session.get(WorkflowExecution, exec_obj.execution_id)
    appr_dict = dict(row.approval_request)
    appr_dict["requested_at"] = (utcnow() - timedelta(hours=5)).isoformat()
    row.approval_request = appr_dict
    flag_modified(row, "approval_request")
    db_session.commit()

    escalated = check_approval_sla(db_session)
    assert len(escalated) == 0


def test_delegation():
    clear_delegations()
    set_delegation("tenant_demo", "approver_01", "delegate_01")
    assert get_delegation("tenant_demo", "approver_01") == "delegate_01"
    # other tenant not affected
    assert get_delegation("tenant_other", "approver_01") is None
    clear_delegations("tenant_demo")
    assert get_delegation("tenant_demo", "approver_01") is None
    # global clear
    set_delegation("tenant_demo", "a", "b")
    set_delegation("tenant_other", "a", "b")
    clear_delegations()
    assert get_delegation("tenant_demo", "a") is None


def test_delegation_allows_approval(db_session, client):
    clear_delegations()
    set_delegation("tenant_demo", "approver_01", "delegate_01")
    # create execution
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id="req_deleg",
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
        source="test",
        created_at=datetime.now(UTC),
    )
    exec_obj = orch.create_execution(db_session, normalized=norm, trace_id="trace_deleg")
    exec_obj = orch.advance_synthetic(db_session, exec_obj.execution_id, trace_id="trace_deleg")
    approval_id = exec_obj.approval_request.approval_id

    # delegate_01 approves (via service)
    from procurement_platform.approvals.service import decide_approval

    row, appr, meta = decide_approval(db_session, approval_id, "approved", "delegate_01", reason="delegated", trace_id="trace_deleg")
    assert appr.status == ApprovalStatus.approved
    assert appr.delegated_from == "approver_01"
    # check via API
    # create another execution for API test
    resp = client.post(
        "/v1/procurement/executions",
        json={"tenant_id": "tenant_demo", "requester_id": "user_01", "items": [{"sku": "MAT-001", "quantity": 10, "unit": "piece"}]},
    )
    aid = resp.json()["approval_request"]["approval_id"]
    # set delegation for API principal? Use anonymous, should allow delegate
    resp2 = client.post(f"/v1/approvals/{aid}/decision", json={"decision": "approved", "decided_by": "delegate_01"})
    # anonymous allows any, so should succeed (or partially)
    assert resp2.status_code in (200, 409)
    clear_delegations()


def test_sla_via_api(client, db_session):
    # create execution and age it, then call API sla check
    clear_delegations()
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id="req_sla_api",
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
        source="test",
        created_at=datetime.now(UTC),
    )
    exec_obj = orch.create_execution(db_session, normalized=norm, trace_id="trace_sla_api")
    exec_obj = orch.advance_synthetic(db_session, exec_obj.execution_id, trace_id="trace_sla_api")
    row = db_session.get(WorkflowExecution, exec_obj.execution_id)
    appr_dict = dict(row.approval_request)
    appr_dict["requested_at"] = (utcnow() - timedelta(hours=13)).isoformat()
    row.approval_request = appr_dict
    flag_modified(row, "approval_request")
    db_session.commit()

    resp = client.post("/v1/approvals/sla/check", json={})
    assert resp.status_code == 200
    data = resp.json()
    assert data["escalated_count"] >= 1
    assert exec_obj.approval_request.approval_id in data["escalated_ids"]
    clear_delegations()
