"""Approval Service — Fase 5 (§11).

Maneja ciclo de vida completo:
- creación con snapshot inmutable + scope_hash
- validación vigencia/scope/estado
- expiración automática
- rechazo terminal, needs_changes, doble aprobación para riesgo alto
- reanudación durable e idempotencia con locks
- auditoría correlacionada
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

from procurement_platform.audit.service import create_audit_event
from procurement_platform.domain.models import (
    ApprovalRequest,
    ApprovalStatus,
    ExecutionState,
    Proposal,
    new_id,
    utcnow,
)
from procurement_platform.persistence.models import WorkflowExecution

# ---------------------------------------------------------------------------
# Lock manager — Fase 5 in-memory, Fase 1-3 abstraction to Redis via infra.locks
# Mantiene _locks para compatibilidad tests, pero delega a LockManager para prod
# ---------------------------------------------------------------------------
_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


def _get_lock(execution_id: str) -> threading.Lock:
    with _locks_guard:
        if execution_id not in _locks:
            _locks[execution_id] = threading.Lock()
        return _locks[execution_id]


def _get_lock_manager():
    try:
        from procurement_platform.infra.locks.manager import get_lock_manager

        return get_lock_manager()
    except Exception:
        return None


class ApprovalError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.details = details or {}


def compute_required_approvals(proposal: Proposal) -> int:
    """Fase 5: doble aprobación si riesgo alto o total elevado."""
    # Si risk high → 2 aprobaciones; si medium y total > 3000 → 2; else 1
    if proposal.risk_level == "high":
        return 2
    if proposal.risk_level == "medium" and proposal.total > 3000:
        return 2
    return 1


def create_approval_request(
    *,
    proposal: Proposal,
    execution_id: str,
    request_id: str,
    requested_by: str = "system",
    expires_in_hours: int = 24,
) -> ApprovalRequest:
    now = utcnow()
    # snapshot inmutable: copia del proposal completo
    snapshot = proposal.model_dump(mode="json")
    required = compute_required_approvals(proposal)
    appr = ApprovalRequest(
        approval_id=new_id("appr"),
        proposal_id=proposal.proposal_id,
        execution_id=execution_id,
        request_id=request_id,
        status=ApprovalStatus.pending,
        scope_hash=proposal.scope_hash,
        requested_by=requested_by,
        requested_at=now,
        expires_at=now + timedelta(hours=expires_in_hours),
        proposal_snapshot=snapshot,
        risk_level=proposal.risk_level,
        total=proposal.total,
        currency=proposal.currency,
        required_approvals=required,
        approvals_received=0,
        approvers=[],
    )
    return appr


def find_execution_by_approval_id(
    db: Session, approval_id: str
) -> tuple[WorkflowExecution, ApprovalRequest] | None:
    rows = db.query(WorkflowExecution).all()
    for row in rows:
        appr_dict = row.approval_request
        if appr_dict and appr_dict.get("approval_id") == approval_id:
            appr = ApprovalRequest.model_validate(appr_dict)
            return row, appr
    return None


def is_expired(appr: ApprovalRequest, now: datetime | None = None) -> bool:
    now = now or utcnow()
    # ensure tz aware
    exp = appr.expires_at
    if exp.tzinfo is None:
        exp = exp.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    return now > exp


def check_and_expire(db: Session, execution_id: str, trace_id: str | None = None) -> bool:
    """Si la aprobación pendiente expiró, transiciona a EXPIRED y retorna True."""
    from procurement_platform.domain.models import is_valid_transition

    row = db.get(WorkflowExecution, execution_id)
    if not row or not row.approval_request:
        return False
    appr = ApprovalRequest.model_validate(row.approval_request)
    if appr.status != ApprovalStatus.pending:
        return False
    if not is_expired(appr):
        return False
    # marcar expirada
    appr.status = ApprovalStatus.expired
    appr.decided_at = utcnow()
    appr.decision_reason = "approval_expired_auto"
    row.approval_request = appr.model_dump(mode="json")
    db.flush()
    # transición de ejecución si está en AWAITING_APPROVAL → EXPIRED
    current = ExecutionState(row.status)
    if is_valid_transition(current, ExecutionState.EXPIRED):
        row.status = ExecutionState.EXPIRED.value
        row.updated_at = utcnow()
        if trace_id:
            row.trace_id = trace_id
        db.flush()
        create_audit_event(
            db,
            execution_id=row.execution_id,
            request_id=row.request_id,
            event_type="approval.expired",
            actor_type="system",
            actor_id="approval_service",
            trace_id=trace_id,
            details={
                "approval_id": appr.approval_id,
                "scope_hash": appr.scope_hash,
                "expired_at": appr.expires_at.isoformat(),
            },
        )
        db.flush()
        # checkpoint
        from procurement_platform.domain.models import new_id as _new_id

        from procurement_platform.persistence.models import WorkflowCheckpoint

        db.add(
            WorkflowCheckpoint(
                checkpoint_id=_new_id("chk"),
                execution_id=row.execution_id,
                node="wait_for_human_decision",
                state_json={"status": ExecutionState.EXPIRED.value, "reason": "expired"},
                created_at=utcnow(),
            )
        )
        db.flush()
    db.commit()
    return True


def validate_scope_or_raise(proposal: Proposal, appr: ApprovalRequest) -> None:
    if proposal.scope_hash != appr.scope_hash:
        raise ApprovalError(
            "scope_mismatch",
            f"scope_hash mismatch — proposal {proposal.scope_hash} != approval {appr.scope_hash}; se requiere nueva aprobación",
            {"approval_scope": appr.scope_hash, "current_scope": proposal.scope_hash},
        )
    # también validar que snapshot coincide con scope actual (detecta tampering)
    if appr.proposal_snapshot and appr.proposal_snapshot.get("scope_hash") != proposal.scope_hash:
        raise ApprovalError(
            "scope_mismatch",
            "proposal modificada después de aprobación — snapshot no coincide",
            {
                "snapshot_scope": appr.proposal_snapshot.get("scope_hash"),
                "current_scope": proposal.scope_hash,
            },
        )


# ---------------------------------------------------------------------------
# Fase 7 — SLA, escalation y delegation
# ---------------------------------------------------------------------------
# In-memory delegation store: (tenant_id, from_user) -> to_user
_delegation_store: dict[tuple[str, str], str] = {}
_delegation_lock = threading.Lock()

# SLA config
SLA_ESCALATION_AFTER_HOURS = 12
SLA_AUTO_ESCALATE_TO = "manager_01"
SLA_CHECK_INTERVAL_MINUTES = 15


def set_delegation(tenant_id: str, from_user: str, to_user: str) -> None:
    with _delegation_lock:
        _delegation_store[(tenant_id, from_user)] = to_user


def get_delegation(tenant_id: str, from_user: str) -> str | None:
    with _delegation_lock:
        return _delegation_store.get((tenant_id, from_user))


def clear_delegations(tenant_id: str | None = None) -> None:
    with _delegation_lock:
        if tenant_id is None:
            _delegation_store.clear()
        else:
            for k in list(_delegation_store.keys()):
                if k[0] == tenant_id:
                    del _delegation_store[k]


def resolve_delegate(tenant_id: str, user: str) -> str:
    """Si user tiene delegación, retorna delegate; sino retorna user."""
    with _delegation_lock:
        return _delegation_store.get((tenant_id, user), user)


def get_sla_age_hours(appr: ApprovalRequest, now: datetime | None = None) -> float:
    now = now or utcnow()
    requested = appr.requested_at
    if requested.tzinfo is None:
        requested = requested.replace(tzinfo=UTC)
    if now.tzinfo is None:
        now = now.replace(tzinfo=UTC)
    delta = now - requested
    return round(delta.total_seconds() / 3600, 2)


def check_approval_sla(db: Session, now: datetime | None = None, trace_id: str | None = None) -> list[str]:
    """Job cada 15m (ARQ) — escanea aprobaciones pending >12h y escala a manager_01.

    Retorna lista de approval_ids escalados. Crea audit `approval.escalated`.
    """
    now = now or utcnow()
    escalated: list[str] = []
    rows = db.query(WorkflowExecution).all()
    for row in rows:
        if not row.approval_request:
            continue
        appr_dict = row.approval_request
        # only pending
        if appr_dict.get("status") != ApprovalStatus.pending.value:
            continue
        # skip already escalated
        if appr_dict.get("escalated_to"):
            continue
        try:
            appr = ApprovalRequest.model_validate(appr_dict)
        except Exception:
            continue
        age = get_sla_age_hours(appr, now)
        if age >= SLA_ESCALATION_AFTER_HOURS:
            # escalate
            escalated_to = SLA_AUTO_ESCALATE_TO
            # also check delegation for escalated user? For now fixed manager
            appr_dict["escalated_to"] = escalated_to
            appr_dict["escalated_at"] = now.isoformat()
            appr_dict["sla_age_hours"] = age
            row.approval_request = appr_dict
            # also update via flag_modified for JSON
            try:
                from sqlalchemy.orm.attributes import flag_modified

                flag_modified(row, "approval_request")
            except Exception:
                pass
            db.flush()
            create_audit_event(
                db,
                execution_id=row.execution_id,
                request_id=row.request_id,
                event_type="approval.escalated",
                actor_type="system",
                actor_id="sla_checker",
                trace_id=trace_id,
                details={
                    "approval_id": appr.approval_id,
                    "from": appr.requested_by,
                    "escalated_to": escalated_to,
                    "age_hours": age,
                    "scope_hash": appr.scope_hash,
                },
            )
            db.flush()
            # notify escalated user (best effort)
            try:
                from procurement_platform.notifications.service import get_notifier

                get_notifier().notify_approval_requested(
                    approval_id=appr.approval_id,
                    execution_id=row.execution_id,
                    request_id=row.request_id,
                    tenant_id=row.tenant_id,
                    total=appr.total or 0,
                    currency=appr.currency or "USD",
                    risk_level=appr.risk_level or "low",
                    scope_hash=appr.scope_hash,
                    required_approvals=appr.required_approvals,
                    trace_id=trace_id,
                )
            except Exception:
                pass
            escalated.append(appr.approval_id)
    if escalated:
        db.commit()
    return escalated


def decide_approval(
    db: Session,
    approval_id: str,
    decision: str,
    decided_by: str,
    reason: str | None = None,
    trace_id: str | None = None,
) -> tuple[WorkflowExecution, ApprovalRequest, dict[str, Any]]:
    """Decide aprobación con locks, expiración, scope validation y doble aprobación.

    Retorna (row, appr, response_meta). Lanza ApprovalError en casos 409/400.
    Es idempotente dentro de la transacción si ya está decidido con mismos args.
    """
    found = find_execution_by_approval_id(db, approval_id)
    if not found:
        raise ApprovalError("not_found", f"approval {approval_id} not found")
    row, appr = found
    execution_id = row.execution_id
    # lock per execution to avoid races — via LockManager (memory or redis)
    mgr = _get_lock_manager()
    lock_key = f"approval:{execution_id}"
    if mgr is not None:
        acquired = mgr.acquire(lock_key, blocking=False, timeout=1.0)
    else:
        lock = _get_lock(execution_id)
        acquired = lock.acquire(blocking=False)
    if not acquired:
        raise ApprovalError(
            "conflict",
            "execution locked — concurrent approval decision in progress",
            {"execution_id": execution_id},
        )
    try:
        # check expiration first
        if check_and_expire(db, execution_id, trace_id=trace_id):
            # re-fetch after expire
            row = db.get(WorkflowExecution, execution_id)  # type: ignore
            appr = ApprovalRequest.model_validate(row.approval_request)  # type: ignore
            raise ApprovalError(
                "expired",
                "approval expired — cannot decide",
                {"approval_id": approval_id, "status": appr.status.value},
            )

        # reload current proposal
        if not row.proposal:
            raise ApprovalError("no_proposal", "proposal missing for approval")
        proposal = Proposal.model_validate(row.proposal)

        # already decided?
        if appr.status != ApprovalStatus.pending:
            # idempotency: if same decision by same actor, return current state without error
            if appr.status.value == decision and appr.decided_by == decided_by:
                return row, appr, {"idempotent": True, "already_decided": True}
            # if already approved and trying to approve again for double approval, handle counts
            if appr.status == ApprovalStatus.approved:
                raise ApprovalError(
                    "already_decided",
                    f"approval already {appr.status.value}",
                    {"status": appr.status.value},
                )
            raise ApprovalError(
                "already_decided",
                f"approval already {appr.status.value}",
                {"status": appr.status.value},
            )

        # validate decision
        if decision not in {"approved", "rejected", "needs_changes"}:
            raise ApprovalError("validation_error", f"invalid decision {decision}")

        # scope validation for any decision; for rejected/needs_changes scope mismatch still blocks? we enforce only for approved
        if decision == "approved":
            validate_scope_or_raise(proposal, appr)

        # Fase 7 — resolve delegation / escalation (for audit)
        delegated_from = None
        try:
            tenant_id = row.tenant_id
            with _delegation_lock:
                for (t, from_u), to_u in list(_delegation_store.items()):
                    if t == tenant_id and to_u == decided_by:
                        delegated_from = from_u
                        break
            # also if escalated, mark
            if not delegated_from and appr.escalated_to and decided_by == appr.escalated_to:
                delegated_from = f"escalated:{appr.requested_by}"
        except Exception:
            delegated_from = None

        # handle decision
        now = utcnow()
        if decision == "approved":
            # doble aprobación lógica
            required = appr.required_approvals
            # si ya hay approvers y el mismo approver intenta de nuevo, idempotente
            if decided_by in appr.approvers:
                raise ApprovalError(
                    "already_approved_by",
                    f"{decided_by} already approved",
                    {"approvers": appr.approvers},
                )
            appr.approvers.append(decided_by)
            appr.approvals_received = len(appr.approvers)
            appr.decided_by = decided_by  # último aprobador
            appr.decision_reason = reason or "approved"
            appr.decided_at = now
            if delegated_from:
                appr.delegated_from = delegated_from

            if appr.approvals_received < required:
                # aún falta otra aprobación → mantener pending, pero audit parcial
                row.approval_request = appr.model_dump(mode="json")
                db.flush()
                create_audit_event(
                    db,
                    execution_id=row.execution_id,
                    request_id=row.request_id,
                    event_type="approval.partially_approved",
                    actor_type="human",
                    actor_id=decided_by,
                    trace_id=trace_id,
                    details={
                        "approval_id": approval_id,
                        "approvers": appr.approvers,
                        "required": required,
                        "received": appr.approvals_received,
                        "scope_hash": appr.scope_hash,
                    },
                )
                db.flush()
                db.commit()
                return (
                    row,
                    appr,
                    {"partial": True, "required": required, "received": appr.approvals_received},
                )
            # fully approved
            appr.status = ApprovalStatus.approved
            row.approval_request = appr.model_dump(mode="json")
            db.flush()
            create_audit_event(
                db,
                execution_id=row.execution_id,
                request_id=row.request_id,
                event_type="approval.decided",
                actor_type="human",
                actor_id=decided_by,
                trace_id=trace_id,
                details={
                    "approval_id": approval_id,
                    "decision": decision,
                    "reason": reason,
                    "scope_hash": appr.scope_hash,
                    "approvers": appr.approvers,
                },
            )
            db.flush()
        elif decision == "rejected":
            appr.status = ApprovalStatus.rejected
            appr.decided_by = decided_by
            appr.decision_reason = reason or "rejected"
            appr.decided_at = now
            appr.approvers.append(decided_by)
            appr.approvals_received = len(appr.approvers)
            if delegated_from:
                appr.delegated_from = delegated_from
            row.approval_request = appr.model_dump(mode="json")
            db.flush()
            create_audit_event(
                db,
                execution_id=row.execution_id,
                request_id=row.request_id,
                event_type="approval.decided",
                actor_type="human",
                actor_id=decided_by,
                trace_id=trace_id,
                details={
                    "approval_id": approval_id,
                    "decision": decision,
                    "reason": reason,
                    "scope_hash": appr.scope_hash,
                },
            )
            db.flush()
        else:  # needs_changes
            # need_changes keeps approval pending? Actually spec says needs_changes -> NEEDS_CLARIFICATION, approval marked needs_changes
            appr.status = ApprovalStatus.needs_changes  # type: ignore — need to allow value
            appr.decided_by = decided_by
            appr.decision_reason = reason or "needs_changes"
            appr.decided_at = now
            if delegated_from:
                appr.delegated_from = delegated_from
            row.approval_request = appr.model_dump(mode="json")
            db.flush()
            create_audit_event(
                db,
                execution_id=row.execution_id,
                request_id=row.request_id,
                event_type="approval.needs_changes",
                actor_type="human",
                actor_id=decided_by,
                trace_id=trace_id,
                details={
                    "approval_id": approval_id,
                    "reason": reason,
                    "scope_hash": appr.scope_hash,
                },
            )
            db.flush()

        db.commit()
        # refresh
        db.refresh(row)
        appr = ApprovalRequest.model_validate(row.approval_request)
        return row, appr, {"idempotent": False}
    finally:
        if mgr is not None:
            try:
                mgr.release(lock_key)
            except Exception:
                pass
        else:
            try:
                lock.release()
            except Exception:
                pass
