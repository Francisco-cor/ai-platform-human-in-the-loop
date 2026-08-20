"""Tests Fase 5 — aprobación con snapshot, scope_hash, expiración, doble aprobación e idempotencia."""

import pytest
from datetime import timedelta

from procurement_platform.approvals.service import compute_required_approvals, is_expired
from procurement_platform.domain.models import (
    ApprovalRequest,
    ApprovalStatus,
    ExecutionState,
    Proposal,
    ProposalLine,
    new_id,
    utcnow,
)
from procurement_platform.persistence.database import get_sessionmaker
from procurement_platform.workflows.orchestrator import WorkflowOrchestrator
from procurement_platform.tools.gateway import ToolGateway, ToolGatewayError


def _proposal(risk="low", total=100, supplier_id="supplier_demo") -> Proposal:
    line = ProposalLine(sku="MAT-001", quantity=10, unit="piece", unit_price=10, currency="USD")
    # compute scope hash
    payload = {
        "proposal_id": "prop_test",
        "supplier_id": supplier_id,
        "lines": [{"sku": line.sku, "quantity": line.quantity, "unit_price": line.unit_price}],
        "total": total,
        "currency": "USD",
    }
    import hashlib
    import json

    scope = "sha256:" + hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
    return Proposal(
        proposal_id="prop_test",
        request_id="req_test",
        execution_id="exec_test",
        supplier_id=supplier_id,
        supplier_name="Demo",
        evidence="test",
        lines=[line],
        subtotal=total,
        tax=0,
        total=total,
        currency="USD",
        confidence=0.9,
        policies_applied=[],
        policy_versions={},
        risk_level=risk,  # type: ignore
        requires_human_approval=True,
        scope_hash=scope,
    )


def test_compute_required_approvals():
    assert compute_required_approvals(_proposal(risk="low", total=100)) == 1
    assert compute_required_approvals(_proposal(risk="high", total=100)) == 2
    assert compute_required_approvals(_proposal(risk="medium", total=4000)) == 2
    assert compute_required_approvals(_proposal(risk="medium", total=100)) == 1


def test_approval_snapshot_and_scope():
    prop = _proposal(risk="low", total=1380)
    from procurement_platform.approvals.service import create_approval_request

    appr = create_approval_request(proposal=prop, execution_id="exec_1", request_id="req_1")
    assert appr.proposal_snapshot is not None
    assert appr.proposal_snapshot["proposal_id"] == prop.proposal_id
    assert appr.scope_hash == prop.scope_hash
    assert appr.is_scope_valid(prop) is True
    # tamper prop
    prop2 = prop.model_copy()
    prop2.scope_hash = "sha256:tampered"
    assert appr.is_scope_valid(prop2) is False


def test_approval_expiration():
    prop = _proposal()
    from procurement_platform.approvals.service import create_approval_request

    appr = create_approval_request(
        proposal=prop, execution_id="exec_1", request_id="req_1", expires_in_hours=1
    )
    assert not is_expired(appr)
    # simulate expired
    appr.expires_at = utcnow() - timedelta(hours=2)
    assert is_expired(appr)


def test_approval_double_requires_two():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    # Force high risk via manual tampering to test double approval logic (determinista sin LLM)
    orch = WorkflowOrchestrator()
    from procurement_platform.domain.models import NormalizedRequest
    from sqlalchemy.orm.attributes import flag_modified
    from procurement_platform.persistence.models import WorkflowExecution

    norm = NormalizedRequest(
        request_id=new_id("req"),
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[{"sku": "MAT-001", "quantity": 10, "unit": "piece"}],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
    )
    exec_obj = orch.create_execution(db, normalized=norm)
    exec_obj = orch.advance_synthetic(db, exec_obj.execution_id)
    # patch to high risk / required 2
    row = db.get(WorkflowExecution, exec_obj.execution_id)
    appr = dict(row.approval_request)
    appr["risk_level"] = "high"
    appr["required_approvals"] = 2
    row.approval_request = appr
    flag_modified(row, "approval_request")
    prop = dict(row.proposal)
    prop["risk_level"] = "high"
    row.proposal = prop
    flag_modified(row, "proposal")
    db.commit()
    exec_obj = orch.get_execution(db, exec_obj.execution_id)
    assert exec_obj.approval_request is not None
    assert exec_obj.approval_request.required_approvals == 2
    assert exec_obj.approval_request.risk_level == "high"
    # first approval -> partially approved, still awaiting
    exec_after_first = orch.approve_and_complete(
        db, exec_obj.execution_id, decided_by="approver_01"
    )
    assert exec_after_first.status == ExecutionState.AWAITING_APPROVAL
    assert exec_after_first.approval_request.status == ApprovalStatus.pending
    assert exec_after_first.approval_request.approvals_received == 1
    # second approver -> should complete
    exec_after_second = orch.approve_and_complete(
        db, exec_obj.execution_id, decided_by="approver_02"
    )
    assert exec_after_second.status == ExecutionState.COMPLETED
    assert exec_after_second.approval_request.status == ApprovalStatus.approved
    assert exec_after_second.approval_request.approvals_received == 2
    db.close()


def test_approval_scope_mismatch_blocks():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    orch = WorkflowOrchestrator()
    from procurement_platform.domain.models import NormalizedRequest

    norm = NormalizedRequest(
        request_id=new_id("req"),
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[{"sku": "MAT-001", "quantity": 10, "unit": "piece"}],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
    )
    exec_obj = orch.create_execution(db, normalized=norm)
    exec_obj = orch.advance_synthetic(db, exec_obj.execution_id)
    approval_id = exec_obj.approval_request.approval_id
    execution_id = exec_obj.execution_id
    # tamper proposal after approval request
    from procurement_platform.persistence.models import WorkflowExecution

    row = db.get(WorkflowExecution, execution_id)
    prop = Proposal.model_validate(row.proposal)  # type: ignore
    # change supplier to cause scope mismatch
    prop.supplier_id = "supplier_tampered"
    # keep old scope_hash? need to also change scope_hash to simulate tampering
    prop.scope_hash = "sha256:tampered_after_approval"
    row.proposal = prop.model_dump(mode="json")
    db.commit()
    # now approving should fail scope_mismatch
    with pytest.raises(ValueError, match="scope_mismatch"):
        orch.approve_and_complete(db, execution_id, decided_by="approver_01")
    # execution should still be awaiting (not completed)
    assert orch.get_execution(db, execution_id).status == ExecutionState.AWAITING_APPROVAL
    db.close()


def test_approval_expiration_blocks_execution():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    orch = WorkflowOrchestrator()
    from procurement_platform.domain.models import NormalizedRequest

    norm = NormalizedRequest(
        request_id=new_id("req"),
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[{"sku": "MAT-001", "quantity": 10, "unit": "piece"}],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
    )
    exec_obj = orch.create_execution(db, normalized=norm)
    exec_obj = orch.advance_synthetic(db, exec_obj.execution_id)
    execution_id = exec_obj.execution_id
    # expire manually
    from procurement_platform.persistence.models import WorkflowExecution

    row = db.get(WorkflowExecution, execution_id)
    appr = ApprovalRequest.model_validate(row.approval_request)  # type: ignore
    appr.expires_at = utcnow() - timedelta(hours=1)
    row.approval_request = appr.model_dump(mode="json")
    db.commit()
    # approve should fail expired
    with pytest.raises(ValueError, match="expired"):
        orch.approve_and_complete(db, execution_id, decided_by="approver_01")
    # after check, execution should be EXPIRED
    exec_after = orch.get_execution(db, execution_id)
    # orchestrator's check_and_expire should have transitioned to EXPIRED when we tried to approve (or on next get)
    # call resume to trigger expire check
    orch._check_and_expire_if_needed(db, row)
    exec_after = orch.get_execution(db, execution_id)
    assert exec_after.status == ExecutionState.EXPIRED
    db.close()


def test_gateway_requires_approval():
    gw = ToolGateway()
    # submit without approval must fail
    with pytest.raises(ToolGatewayError, match="approval_required"):
        gw.call(
            tool_name="submit_purchase_order",
            payload={"proposal_id": "prop_1"},
            execution_id="exec_1",
            state=ExecutionState.POLICY_CHECKED,
            has_approval=False,
        )
    # with approval passes (state APPROVED allows)
    assert (
        gw.call(
            tool_name="submit_purchase_order",
            payload={"proposal_id": "prop_1"},
            execution_id="exec_1",
            state=ExecutionState.APPROVED,
            has_approval=True,
        )
        is not None
    )


def test_gateway_idempotency_submit():
    gw = ToolGateway()
    payload = {"proposal_id": "prop_dup"}
    r1 = gw.call(
        tool_name="submit_purchase_order",
        payload=payload,
        execution_id="exec_dup",
        state=ExecutionState.APPROVED,
        has_approval=True,
    )
    r2 = gw.call(
        tool_name="submit_purchase_order",
        payload=payload,
        execution_id="exec_dup",
        state=ExecutionState.APPROVED,
        has_approval=True,
    )
    assert r1 == r2
    assert len([c for c in gw.call_log if c["execution_id"] == "exec_dup"]) == 1


def test_resume_durable_idempotent():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    orch = WorkflowOrchestrator()
    from procurement_platform.domain.models import NormalizedRequest

    norm = NormalizedRequest(
        request_id=new_id("req"),
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[{"sku": "MAT-001", "quantity": 10, "unit": "piece"}],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
    )
    exec_obj = orch.create_execution(db, normalized=norm)
    exec_obj = orch.advance_synthetic(db, exec_obj.execution_id)
    execution_id = exec_obj.execution_id
    # approve first time
    exec_after = orch.approve_and_complete(db, execution_id, decided_by="approver_01")
    assert exec_after.status == ExecutionState.COMPLETED
    # resume should be idempotent, not duplicate order
    exec_resume = orch.resume_durable(db, execution_id)
    assert exec_resume.status == ExecutionState.COMPLETED
    # gateway call log for this execution should have only 1 submit
    from procurement_platform.tools.gateway import _GLOBAL_CALL_LOG

    submits = [
        c
        for c in _GLOBAL_CALL_LOG
        if c["execution_id"] == execution_id and c["tool"] == "submit_purchase_order"
    ]
    assert len(submits) == 1
    db.close()


def test_reject_terminal():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    orch = WorkflowOrchestrator()
    from procurement_platform.domain.models import NormalizedRequest

    norm = NormalizedRequest(
        request_id=new_id("req"),
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[{"sku": "MAT-001", "quantity": 10, "unit": "piece"}],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
    )
    exec_obj = orch.create_execution(db, normalized=norm)
    exec_obj = orch.advance_synthetic(db, exec_obj.execution_id)
    execution_id = exec_obj.execution_id
    exec_rejected = orch.reject_execution(
        db, execution_id, decided_by="approver_01", reason="budget"
    )
    assert exec_rejected.status == ExecutionState.REJECTED
    # cannot approve after rejected
    with pytest.raises(ValueError, match="cannot approve"):
        orch.approve_and_complete(db, execution_id, decided_by="approver_02")
    db.close()


def test_needs_changes():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    orch = WorkflowOrchestrator()
    from procurement_platform.domain.models import NormalizedRequest

    norm = NormalizedRequest(
        request_id=new_id("req"),
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[{"sku": "MAT-001", "quantity": 10, "unit": "piece"}],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
    )
    exec_obj = orch.create_execution(db, normalized=norm)
    exec_obj = orch.advance_synthetic(db, exec_obj.execution_id)
    execution_id = exec_obj.execution_id
    exec_nc = orch.request_changes(
        db, execution_id, decided_by="approver_01", reason="need more info"
    )
    assert exec_nc.status == ExecutionState.NEEDS_CLARIFICATION
    db.close()
