"""Minimal workflow orchestrator — runtime propio (Fase 1).

Grafo lineal sintético sin LLM:
RECEIVED -> NORMALIZED -> CONTEXT_LOADED -> ... -> COMPLETED
con validación de transiciones y persistencia durable.

Contratos §5 y §7.
"""
from __future__ import annotations

import hashlib
import json
from datetime import timedelta

from sqlalchemy.orm import Session

from procurement_platform.audit.service import create_audit_event
from procurement_platform.domain.models import (
    ApprovalRequest,
    ApprovalStatus,
    Execution,
    ExecutionState,
    NormalizedRequest,
    Proposal,
    ProposalLine,
    is_valid_transition,
    new_id,
    utcnow,
)
from procurement_platform.persistence.models import WorkflowCheckpoint, WorkflowExecution


def _serialize_execution(exec_obj: WorkflowExecution) -> Execution:
    return Execution(
        execution_id=exec_obj.execution_id,
        request_id=exec_obj.request_id,
        tenant_id=exec_obj.tenant_id,
        status=ExecutionState(exec_obj.status),
        current_node=exec_obj.current_node,
        normalized_request=NormalizedRequest.model_validate(exec_obj.normalized_request)
        if exec_obj.normalized_request
        else None,
        proposal=Proposal.model_validate(exec_obj.proposal) if exec_obj.proposal else None,
        approval_request=ApprovalRequest.model_validate(exec_obj.approval_request)
        if exec_obj.approval_request
        else None,
        created_at=exec_obj.created_at,
        updated_at=exec_obj.updated_at,
        trace_id=exec_obj.trace_id,
    )


class WorkflowOrchestrator:
    """Orquestador sincrónico — Fase 1 sin modelo real."""

    def create_execution(
        self,
        db: Session,
        *,
        normalized: NormalizedRequest,
        trace_id: str | None = None,
        actor_id: str = "system",
    ) -> Execution:
        execution_id = new_id("exec")
        now = utcnow()
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
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
        create_audit_event(
            db,
            execution_id=execution_id,
            request_id=normalized.request_id,
            event_type="execution.created",
            actor_type="system",
            actor_id=actor_id,
            trace_id=trace_id,
            details={"status": ExecutionState.RECEIVED.value},
        )
        # Persist checkpoint
        db.add(
            WorkflowCheckpoint(
                checkpoint_id=new_id("chk"),
                execution_id=execution_id,
                node="intake_request",
                state_json={"status": ExecutionState.RECEIVED.value},
                created_at=now,
            )
        )
        db.commit()
        db.refresh(row)
        return _serialize_execution(row)

    def get_execution(self, db: Session, execution_id: str) -> Execution | None:
        row = db.get(WorkflowExecution, execution_id)
        if not row:
            return None
        return _serialize_execution(row)

    def transition(
        self,
        db: Session,
        execution_id: str,
        target: ExecutionState,
        *,
        node: str | None = None,
        trace_id: str | None = None,
        actor_type: str = "system",
        actor_id: str = "system",
        details: dict | None = None,
    ) -> Execution:
        row = db.get(WorkflowExecution, execution_id)
        if not row:
            raise ValueError(f"execution {execution_id} not found")
        current = ExecutionState(row.status)
        if not is_valid_transition(current, target):
            raise ValueError(f"invalid transition {current.value} -> {target.value}")
        # Invariante §5: nunca ACTION_EXECUTED sin aprobación válida cuando se requiere
        # En Fase 1, POLICY_CHECKED siempre requiere aprobación; se enforce aquí
        if target == ExecutionState.ACTION_EXECUTED:
            if row.approval_request:
                appr = row.approval_request
                if appr.get("status") != ApprovalStatus.approved.value:
                    raise ValueError("cannot execute without approved approval_request")
                # scope_hash must match proposal
                if row.proposal and appr.get("scope_hash") != row.proposal.get("scope_hash"):
                    raise ValueError("scope_hash mismatch — approval expired for current proposal")

        row.status = target.value
        if node:
            row.current_node = node
        row.updated_at = utcnow()
        if trace_id:
            row.trace_id = trace_id
        db.flush()
        create_audit_event(
            db,
            execution_id=execution_id,
            request_id=row.request_id,
            event_type=f"execution.transition.{target.value.lower()}",
            actor_type=actor_type,  # type: ignore
            actor_id=actor_id,
            trace_id=trace_id or row.trace_id,
            details={"from": current.value, "to": target.value, **(details or {})},
        )
        db.add(
            WorkflowCheckpoint(
                checkpoint_id=new_id("chk"),
                execution_id=execution_id,
                node=node or target.value.lower(),
                state_json={"status": target.value, "node": node},
                created_at=utcnow(),
            )
        )
        db.commit()
        db.refresh(row)
        return _serialize_execution(row)

    def advance_synthetic(self, db: Session, execution_id: str, *, trace_id: str | None = None) -> Execution:
        """Avanza una ejecución sintética por el happy path hasta COMPLETED.

        En Fase 1 esto simula el workflow completo sin LLM:
        - NORMALIZED
        - CONTEXT_LOADED
        - POLICY_RETRIEVED
        - SHORTAGE_CALCULATED
        - SUPPLIERS_QUERIED
        - PROPOSAL_DRAFTED (crea Proposal stub)
        - POLICY_CHECKED
        - AWAITING_APPROVAL (crea ApprovalRequest)
        - luego se detiene; el caller debe aprobar y luego continuar a COMPLETED.

        Para simplificar el criterio de salida Fase 1 ("ejecución sintética puede crearse,
        persistirse, consultarse y terminar en estado válido"), este método permite
        avanzar automáticamente hasta COMPLETED si se le pide.

        Aquí implementamos un helper que crea proposal y approval pending.
        """
        row = db.get(WorkflowExecution, execution_id)
        if not row:
            raise ValueError("not found")
        # Si ya está terminal, no hacer nada
        current = ExecutionState(row.status)
        if current in {ExecutionState.COMPLETED, ExecutionState.FAILED_TERMINAL}:
            return _serialize_execution(row)

        # Definir secuencia hasta AWAITING_APPROVAL
        seq = [
            (ExecutionState.NORMALIZED, "normalize_request"),
            (ExecutionState.CONTEXT_LOADED, "load_inventory_context"),
            (ExecutionState.POLICY_RETRIEVED, "retrieve_policies"),
            (ExecutionState.SHORTAGE_CALCULATED, "calculate_shortage"),
            (ExecutionState.SUPPLIERS_QUERIED, "query_suppliers"),
            (ExecutionState.PROPOSAL_DRAFTED, "draft_order_proposals"),
            (ExecutionState.POLICY_CHECKED, "run_deterministic_policy_checks"),
            (ExecutionState.AWAITING_APPROVAL, "wait_for_human_decision"),
        ]
        for target, node in seq:
            cur = ExecutionState(db.get(WorkflowExecution, execution_id).status)  # type: ignore
            if is_valid_transition(cur, target):
                # Antes de PROPOSAL_DRAFTED, crear proposal stub
                if target == ExecutionState.PROPOSAL_DRAFTED and not row.proposal:
                    proposal = self._build_synthetic_proposal(row)
                    row.proposal = proposal.model_dump(mode="json")
                    db.flush()
                if target == ExecutionState.AWAITING_APPROVAL and not row.approval_request:
                    # requiere proposal
                    if not row.proposal:
                        raise RuntimeError("proposal required before approval")
                    prop = Proposal.model_validate(row.proposal)
                    appr = ApprovalRequest(
                        approval_id=new_id("appr"),
                        proposal_id=prop.proposal_id,
                        execution_id=execution_id,
                        request_id=row.request_id,
                        status=ApprovalStatus.pending,
                        scope_hash=prop.scope_hash,
                        requested_by="system",
                        requested_at=utcnow(),
                        expires_at=utcnow() + timedelta(hours=24),
                    )
                    row.approval_request = appr.model_dump(mode="json")
                    db.flush()
                self.transition(db, execution_id, target, node=node, trace_id=trace_id)

        return self.get_execution(db, execution_id)  # type: ignore

    def _build_synthetic_proposal(self, row: WorkflowExecution) -> Proposal:
        normalized = NormalizedRequest.model_validate(row.normalized_request) if row.normalized_request else None
        # defaults
        items = normalized.items if normalized else []
        # synthetic supplier
        lines = []
        for it in items:
            lines.append(
                ProposalLine(
                    sku=it.sku,
                    quantity=it.quantity,
                    unit=it.unit,
                    unit_price=10.0,  # synthetic price
                    currency=normalized.currency if normalized else "USD",
                )
            )
        if not lines:
            lines = [ProposalLine(sku="MAT-001", quantity=10, unit="piece", unit_price=10.0, currency="USD")]
        subtotal = sum(li.quantity * li.unit_price for li in lines)
        total = round(subtotal, 2)
        proposal_id = new_id("prop")
        scope_payload = {
            "proposal_id": proposal_id,
            "supplier_id": "supplier_demo",
            "lines": [{"sku": li.sku, "quantity": li.quantity, "unit_price": li.unit_price} for li in lines],
            "total": total,
            "currency": lines[0].currency,
        }
        scope_hash = "sha256:" + hashlib.sha256(json.dumps(scope_payload, sort_keys=True).encode()).hexdigest()
        return Proposal(
            proposal_id=proposal_id,
            request_id=row.request_id,
            execution_id=row.execution_id,
            supplier_id="supplier_demo",
            supplier_name="Demo Supplier Inc.",
            evidence="synthetic — Fase 1 stub",
            lines=lines,
            subtotal=subtotal,
            tax=0,
            total=total,
            currency=lines[0].currency,
            confidence=0.95,
            policies_applied=["budget_limit:v1", "supplier_allowlist:v1"],
            policy_versions={"budget_limit": "1.0.0"},
            assumptions=["synthetic pricing"],
            missing_data=[],
            risk_level="low",
            requires_human_approval=True,
            scope_hash=scope_hash,
        )

    def approve_and_complete(self, db: Session, execution_id: str, *, decided_by: str, trace_id: str | None = None) -> Execution:
        row = db.get(WorkflowExecution, execution_id)
        if not row:
            raise ValueError("not found")
        if ExecutionState(row.status) != ExecutionState.AWAITING_APPROVAL:
            raise ValueError(f"cannot approve in state {row.status}")
        appr_dict = row.approval_request
        if not appr_dict:
            raise ValueError("no approval_request")
        # mark approved
        appr = ApprovalRequest.model_validate(appr_dict)
        appr.status = ApprovalStatus.approved
        appr.decided_by = decided_by
        appr.decided_at = utcnow()
        appr.decision_reason = "approved in synthetic flow"
        row.approval_request = appr.model_dump(mode="json")
        db.flush()
        # transitions
        self.transition(db, execution_id, ExecutionState.APPROVED, node="wait_for_human_decision", trace_id=trace_id, actor_type="human", actor_id=decided_by)
        self.transition(db, execution_id, ExecutionState.ACTION_EXECUTED, node="execute_purchase_order", trace_id=trace_id)
        self.transition(db, execution_id, ExecutionState.VERIFIED, node="verify_execution", trace_id=trace_id)
        self.transition(db, execution_id, ExecutionState.COMPLETED, node="summarize_and_close", trace_id=trace_id)
        return self.get_execution(db, execution_id)  # type: ignore
