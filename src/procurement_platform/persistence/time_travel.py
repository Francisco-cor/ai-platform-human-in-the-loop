"""Time-travel query for execution — Fase 9 Data Platform.

Reconstruye estado de ejecución a un timestamp dado desde audit_events + checkpoints.
Útil para debugging: "¿por qué se aprobó esto hace 3 días?"

  get_execution_at(execution_id, timestamp) -> Execution | None
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from procurement_platform.domain.models import Execution, ExecutionState
from procurement_platform.persistence.models import AuditEventRow, WorkflowCheckpoint, WorkflowExecution


def _parse_ts(ts: str | datetime) -> datetime:
    if isinstance(ts, datetime):
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=UTC)
        return ts
    # ISO
    try:
        dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=UTC)
        return dt
    except Exception:
        return datetime.now(UTC)


def get_execution_at(db: Session, execution_id: str, at: str | datetime) -> dict[str, Any] | None:
    """Reconstruye ejecución a timestamp `at` desde audit + checkpoints.

    Retorna dict con snapshot de WorkflowExecution + estado derivado de eventos.
    Si no hay datos, retorna None.
    """
    at_dt = _parse_ts(at)
    row = db.get(WorkflowExecution, execution_id)
    if not row:
        return None

    # Si at es posterior a updated_at, retorna estado actual
    # Si at es anterior a created_at, no existe aún
    created = row.created_at
    if created and created.tzinfo is None:
        created = created.replace(tzinfo=UTC)
    if at_dt < created:
        return None

    # Reconstruir estado desde audit_events hasta at
    events = (
        db.query(AuditEventRow)
        .filter(AuditEventRow.execution_id == execution_id, AuditEventRow.timestamp <= at_dt)
        .order_by(AuditEventRow.timestamp.asc(), AuditEventRow.event_id.asc())
        .all()
    )
    # Derivar estado desde último execution.transition.* o desde checkpoints
    # Simplificación: último estado visto en events, fallback a row.status
    last_state = None
    last_node = None
    for ev in events:
        if ev.event_type.startswith("execution.transition."):
            # event_type like execution.transition.completed
            state_name = ev.event_type.split(".")[-1].upper()
            try:
                last_state = ExecutionState[state_name]
            except Exception:
                pass
            # details may contain node
            if ev.details and isinstance(ev.details, dict):
                last_node = ev.details.get("node") or last_node
        elif ev.event_type == "execution.created":
            last_state = ExecutionState.RECEIVED
            last_node = "intake_request"

    # Also check checkpoints
    checkpoints = (
        db.query(WorkflowCheckpoint)
        .filter(WorkflowCheckpoint.execution_id == execution_id, WorkflowCheckpoint.created_at <= at_dt)
        .order_by(WorkflowCheckpoint.created_at.asc())
        .all()
    )
    if checkpoints:
        last_cp = checkpoints[-1]
        # checkpoint state_json may have status
        state_str = last_cp.state_json.get("status") if isinstance(last_cp.state_json, dict) else None
        if state_str:
            try:
                last_state = ExecutionState[state_str]
            except Exception:
                pass
        last_node = last_cp.node or last_node

    # Fallback to current row if no events
    if last_state is None:
        try:
            last_state = ExecutionState(row.status)
        except Exception:
            last_state = None
        last_node = row.current_node

    # Build snapshot
    # Use row's normalized_request/proposal/approval_request but filter by timestamp? For time-travel,
    # we should return the state of those JSON as of at — but they are updated in-place, not versioned.
    # For MVP, we return current JSON but mark as of at, and include event history.
    # More accurate would require versioning, but we approximate via events details.
    # For debugging, we include relevant event details that changed proposal/approval.

    # Collect proposal/approval snapshots from events details if available
    # e.g., proposal.drafted, approval.requested etc. contain proposal data
    # For now, just return row's current JSON plus derived state

    return {
        "execution_id": row.execution_id,
        "request_id": row.request_id,
        "tenant_id": row.tenant_id,
        "status": last_state.value if last_state else row.status,
        "current_node": last_node or row.current_node,
        "at": at_dt.isoformat(),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "trace_id": row.trace_id,
        "normalized_request": row.normalized_request,
        "proposal": row.proposal,
        "approval_request": row.approval_request,
        "events_count": len(events),
        "checkpoints_count": len(checkpoints),
        "events": [
            {"event_type": e.event_type, "timestamp": e.timestamp.isoformat(), "actor_id": e.actor_id, "details": e.details}
            for e in events[-10:]  # last 10 for context
        ],
    }


def get_execution_history(db: Session, execution_id: str) -> list[dict[str, Any]]:
    """Retorna historia completa de estados para debugging."""
    row = db.get(WorkflowExecution, execution_id)
    if not row:
        return []
    events = (
        db.query(AuditEventRow)
        .filter(AuditEventRow.execution_id == execution_id)
        .order_by(AuditEventRow.timestamp.asc())
        .all()
    )
    history = []
    for ev in events:
        history.append(
            {
                "timestamp": ev.timestamp.isoformat(),
                "event_type": ev.event_type,
                "actor": f"{ev.actor_type}:{ev.actor_id}",
                "details": ev.details,
            }
        )
    return history
