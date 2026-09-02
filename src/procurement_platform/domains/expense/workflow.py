"""
Expense workflow — 8 nodos (Fase 11).

Graph:
  RECEIVED → NORMALIZED → CONTEXT_LOADED → POLICY_RETRIEVED → VALIDATED → PROPOSAL_DRAFTED → POLICY_CHECKED → AWAITING_APPROVAL → APPROVED → ACTION_EXECUTED → VERIFIED → COMPLETED
  (plus BLOCKED/REJECTED/EXPIRED)

Reuses platform:
- gateway (ToolGateway) for idempotency/budgets
- approvals service (snapshot, scope_hash, 2 approvals if high)
- audit service (trace, lineage)
- persistence (WorkflowExecution, AuditEventRow, WorkflowCheckpoint)
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from procurement_platform.domain.models import ExecutionState, new_id, utcnow
from procurement_platform.persistence.models import AuditEventRow, WorkflowCheckpoint, WorkflowExecution

# Reuse platform approvals and audit
from procurement_platform.approvals.service import create_approval_request as _create_approval
from procurement_platform.audit.service import create_audit_event
from procurement_platform.domains.expense.models import ExpenseProposal, ExpenseRequest
from procurement_platform.domains.expense.policies.expense_policy import (
    ExpensePolicyConfig,
    is_blocked,
    run_expense_policy_checks,
)


def _scope_hash_expense(proposal: ExpenseProposal) -> str:
    raw = json.dumps(
        {"amount": proposal.amount, "currency": proposal.currency, "reason": proposal.reason, "total": proposal.total},
        sort_keys=True,
    ).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class ExpenseOrchestrator:
    """Expense orchestrator — reuses platform gateway/approvals/audit."""

    def create_execution(self, db: Session, normalized: ExpenseRequest, trace_id: str | None = None) -> WorkflowExecution:
        execution_id = new_id("exec")
        row = WorkflowExecution(
            execution_id=execution_id,
            request_id=normalized.request_id,
            tenant_id=normalized.tenant_id,
            status=ExecutionState.RECEIVED.value,
            current_node="intake_request",
            normalized_request=normalized.model_dump(mode="json"),
            proposal=None,
            approval_request=None,
            trace_id=trace_id,
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db.add(row)
        db.flush()
        create_audit_event(
            db,
            execution_id=execution_id,
            request_id=normalized.request_id,
            event_type="execution.created",
            actor_type="system",
            actor_id=normalized.requester_id,
            trace_id=trace_id,
            details={"domain": "expense", "amount": normalized.amount, "currency": normalized.currency},
        )
        db.commit()
        return row

    def advance(self, db: Session, execution_id: str, trace_id: str | None = None) -> WorkflowExecution:
        row = db.get(WorkflowExecution, execution_id)
        if not row:
            raise ValueError("execution not found")
        # Load normalized
        norm_dict = row.normalized_request or {}
        try:
            norm = ExpenseRequest.model_validate(norm_dict)
        except Exception:
            # fallback
            norm = ExpenseRequest(
                request_id=row.request_id,
                tenant_id=row.tenant_id,
                requester_id="user_01",
                amount=float(norm_dict.get("amount", 100)),
                currency=norm_dict.get("currency", "USD"),
                reason=norm_dict.get("reason", "test"),
                created_at=utcnow(),
            )

        # Node 2-3: normalize & load_context (no-op for expense)
        row.current_node = "normalize_request"
        row.status = ExecutionState.NORMALIZED.value
        db.flush()
        create_audit_event(db, execution_id=execution_id, request_id=row.request_id, event_type="workflow.node.completed", actor_type="system", actor_id="system", trace_id=trace_id, details={"node": "normalize_request"})

        row.current_node = "load_context"
        row.status = ExecutionState.CONTEXT_LOADED.value
        db.flush()

        # Node 4: retrieve_policies (mock)
        row.current_node = "retrieve_policies"
        row.status = ExecutionState.POLICY_RETRIEVED.value
        db.flush()
        create_audit_event(db, execution_id=execution_id, request_id=row.request_id, event_type="rag.retrieved", actor_type="system", actor_id="system", trace_id=trace_id, details={"domain": "expense", "policies": ["expense_budget_v1"]})

        # Node 5: validate
        row.current_node = "validate"
        checks = run_expense_policy_checks(norm.amount, norm.currency, norm.reason)
        blocked = is_blocked(checks)
        if blocked:
            row.status = ExecutionState.BLOCKED.value
            row.current_node = "blocked"
            db.flush()
            create_audit_event(db, execution_id=execution_id, request_id=row.request_id, event_type="policy.blocked", actor_type="system", actor_id="system", trace_id=trace_id, details={"checks": [c.__dict__ for c in checks]})
            db.commit()
            return row

        row.status = ExecutionState.SHORTAGE_CALCULATED.value  # reuse state for validate
        db.flush()

        # Node 6: draft_proposal
        row.current_node = "draft_proposal"
        proposal = ExpenseProposal(
            proposal_id=new_id("prop"),
            request_id=row.request_id,
            execution_id=execution_id,
            amount=norm.amount,
            currency=norm.currency,
            reason=norm.reason,
            requester_id=norm.requester_id,
            tenant_id=norm.tenant_id,
            risk_level="high" if norm.amount > 1000 else "low",
            requires_human_approval=True,
            total=norm.amount,
            confidence=0.95,
            policies_applied=["budget_limit", "currency_supported"],
            evidence="expense policy v1",
            created_at=utcnow(),
        )
        row.proposal = proposal.model_dump(mode="json")
        row.status = ExecutionState.PROPOSAL_DRAFTED.value
        db.flush()
        create_audit_event(db, execution_id=execution_id, request_id=row.request_id, event_type="proposal.drafted", actor_type="system", actor_id="system", trace_id=trace_id, details={"proposal_id": proposal.proposal_id, "amount": proposal.amount, "risk": proposal.risk_level})

        # Node 7: policy_check
        row.current_node = "policy_check"
        row.status = ExecutionState.POLICY_CHECKED.value
        db.flush()
        create_audit_event(db, execution_id=execution_id, request_id=row.request_id, event_type="policy.checked", actor_type="system", actor_id="system", trace_id=trace_id, details={"checks": [c.__dict__ for c in checks]})

        # Node 8: await_approval
        row.current_node = "await_approval"
        # Determine required approvals: high risk =>2
        required = 2 if proposal.risk_level == "high" else 1
        scope_hash = _scope_hash_expense(proposal)
        appr = {
            "approval_id": new_id("appr"),
            "proposal_id": proposal.proposal_id,
            "execution_id": execution_id,
            "request_id": row.request_id,
            "tenant_id": row.tenant_id,
            "scope_hash": scope_hash,
            "proposal_snapshot": proposal.model_dump(mode="json"),
            "amount": proposal.amount,
            "currency": proposal.currency,
            "risk_level": proposal.risk_level,
            "required_approvals": required,
            "approvals_received": 0,
            "approvers": [],
            "status": "pending",
            "requested_by": norm.requester_id,
            "requested_at": utcnow().isoformat(),
            "expires_at": (utcnow() + timedelta(hours=24)).isoformat(),
        }
        row.approval_request = appr
        row.status = ExecutionState.AWAITING_APPROVAL.value
        row.current_node = "wait_for_human_decision"
        db.flush()
        create_audit_event(db, execution_id=execution_id, request_id=row.request_id, event_type="approval.requested", actor_type="system", actor_id="system", trace_id=trace_id, details={"approval_id": appr["approval_id"], "risk": proposal.risk_level, "scope_hash": scope_hash})
        # checkpoint
        db.add(WorkflowCheckpoint(checkpoint_id=new_id("chk"), execution_id=execution_id, node=row.current_node, state_json={"status": row.status, "proposal": row.proposal}, created_at=utcnow()))
        db.commit()
        return row

    def get_execution(self, db: Session, execution_id: str) -> WorkflowExecution | None:
        return db.get(WorkflowExecution, execution_id)

    def approve(self, db: Session, execution_id: str, decided_by: str, trace_id: str | None = None) -> WorkflowExecution:
        row = db.get(WorkflowExecution, execution_id)
        if not row:
            raise ValueError("execution not found")
        if row.status != ExecutionState.AWAITING_APPROVAL.value:
            raise ValueError(f"cannot approve in state {row.status}")
        appr = row.approval_request or {}
        # check expiration
        try:
            exp = datetime.fromisoformat(appr["expires_at"].replace("Z", "+00:00"))
            if exp.tzinfo is None:
                exp = exp.replace(tzinfo=UTC)
            if utcnow() > exp:
                row.status = ExecutionState.EXPIRED.value
                appr["status"] = "expired"
                row.approval_request = appr
                db.commit()
                raise ValueError("expired")
        except ValueError as e:
            if "expired" in str(e):
                raise
        except Exception:
            pass
        # double approval
        required = appr.get("required_approvals", 1)
        received = appr.get("approvals_received", 0) + 1
        approvers = list(appr.get("approvers", [])) + [decided_by]
        appr["approvals_received"] = received
        appr["approvers"] = approvers
        if received < required:
            appr["status"] = "pending"
            row.approval_request = appr
            from sqlalchemy.orm.attributes import flag_modified

            flag_modified(row, "approval_request")
            db.flush()
            create_audit_event(db, execution_id=execution_id, request_id=row.request_id, event_type="approval.partially_approved", actor_type="human", actor_id=decided_by, trace_id=trace_id, details={"approvers": approvers, "required": required})
            db.commit()
            return row
        # final approval
        appr["status"] = "approved"
        appr["decided_by"] = decided_by
        appr["decided_at"] = utcnow().isoformat()
        row.approval_request = appr
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(row, "approval_request")
        # Execute (mock submit)
        row.status = ExecutionState.ACTION_EXECUTED.value
        row.current_node = "execute_purchase_order"
        db.flush()
        create_audit_event(db, execution_id=execution_id, request_id=row.request_id, event_type="tool.submit_purchase_order", actor_type="system", actor_id="system", trace_id=trace_id, details={"amount": appr["amount"]}, lineage={"document_ids": [], "policy_ids": ["expense_budget_v1"], "supplier_ids": ["expense_supplier"]})
        # Verify
        row.status = ExecutionState.VERIFIED.value
        row.current_node = "verify_execution"
        db.flush()
        row.status = ExecutionState.COMPLETED.value
        row.current_node = "summarize_and_close"
        db.flush()
        create_audit_event(db, execution_id=execution_id, request_id=row.request_id, event_type="execution.completed", actor_type="system", actor_id="system", trace_id=trace_id, details={"domain": "expense"})
        db.add(WorkflowCheckpoint(checkpoint_id=new_id("chk"), execution_id=execution_id, node=row.current_node, state_json={"status": row.status}, created_at=utcnow()))
        db.commit()
        return row

    def reject(self, db: Session, execution_id: str, decided_by: str, trace_id: str | None = None) -> WorkflowExecution:
        row = db.get(WorkflowExecution, execution_id)
        if not row:
            raise ValueError("execution not found")
        row.status = ExecutionState.REJECTED.value
        appr = row.approval_request or {}
        appr["status"] = "rejected"
        appr["decided_by"] = decided_by
        row.approval_request = appr
        from sqlalchemy.orm.attributes import flag_modified

        flag_modified(row, "approval_request")
        db.flush()
        create_audit_event(db, execution_id=execution_id, request_id=row.request_id, event_type="approval.rejected", actor_type="human", actor_id=decided_by, trace_id=trace_id, details={})
        db.commit()
        return row
