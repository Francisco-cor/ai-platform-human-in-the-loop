"""Audit service — append-only logical events (Fase 1)."""

from __future__ import annotations

import hashlib
import json
from typing import Any

from sqlalchemy.orm import Session

from procurement_platform.domain.models import AuditEvent, new_id, utcnow
from procurement_platform.persistence.models import AuditEventRow


def hash_payload(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def create_audit_event(
    db: Session,
    *,
    execution_id: str,
    request_id: str,
    event_type: str,
    actor_type: str,
    actor_id: str,
    tool_name: str | None = None,
    input_payload: Any | None = None,
    output_payload: Any | None = None,
    policy_decisions: list[str] | None = None,
    model_metadata: dict | None = None,
    trace_id: str | None = None,
    span_id: str | None = None,
    duration_ms: int | None = None,
    details: dict | None = None,
) -> AuditEvent:
    # F5-3: auto-correlate trace_id/span_id from OTEL if not provided
    if not trace_id or not span_id:
        try:
            from procurement_platform.observability.tracing import get_current_span_context

            tid, sid = get_current_span_context()
            if not trace_id and tid:
                trace_id = tid
            if not span_id and sid:
                span_id = sid
        except Exception:
            pass
    # F5-3: ensure model_metadata includes prompt/graph version if missing
    # Fase 6: also include prompt_hash (sha256 of registry file) for traceability & BigQuery lineage
    if model_metadata is None:
        try:
            from procurement_platform.config.settings import get_settings

            s = get_settings()
            model_metadata = {"prompt_version": s.prompt_version, "graph_version": s.graph_version}
            try:
                from procurement_platform.agents.prompts import get_prompt_hash

                model_metadata["prompt_hash"] = get_prompt_hash(s.prompt_version)
            except Exception:
                pass
        except Exception:
            model_metadata = {}
    else:
        # enrich if not already
        try:
            from procurement_platform.config.settings import get_settings

            s = get_settings()
            if "prompt_version" not in model_metadata:
                model_metadata["prompt_version"] = s.prompt_version
            if "graph_version" not in model_metadata:
                model_metadata["graph_version"] = s.graph_version
            if "prompt_hash" not in model_metadata:
                try:
                    from procurement_platform.agents.prompts import get_prompt_hash

                    model_metadata["prompt_hash"] = get_prompt_hash(
                        model_metadata.get("prompt_version", s.prompt_version)
                    )
                except Exception:
                    pass
        except Exception:
            pass
    # include duration_ms and span_id in details for drill-down
    if duration_ms is not None or span_id:
        details = dict(details or {})
        if duration_ms is not None:
            details["duration_ms"] = duration_ms
        if span_id:
            details["span_id"] = span_id
    # Fase 7: redactar PII en details antes de persistir
    try:
        if details:
            from procurement_platform.security.pii import redact_dict_values

            details = redact_dict_values(details)
    except Exception:
        pass
    event = AuditEvent(
        event_id=new_id("evt"),
        execution_id=execution_id,
        request_id=request_id,
        event_type=event_type,
        actor_type=actor_type,  # type: ignore
        actor_id=actor_id,
        tool_name=tool_name,
        input_hash=hash_payload(input_payload) if input_payload is not None else None,
        output_hash=hash_payload(output_payload) if output_payload is not None else None,
        policy_decisions=policy_decisions or [],
        model_metadata=model_metadata,
        timestamp=utcnow(),
        trace_id=trace_id,
        details=details or {},
    )
    row = AuditEventRow(
        event_id=event.event_id,
        execution_id=event.execution_id,
        request_id=event.request_id,
        event_type=event.event_type,
        actor_type=event.actor_type,
        actor_id=event.actor_id,
        tool_name=event.tool_name,
        input_hash=event.input_hash,
        output_hash=event.output_hash,
        policy_decisions=event.policy_decisions,
        model_metadata=event.model_metadata,
        timestamp=event.timestamp,
        trace_id=event.trace_id,
        details=event.details,
    )
    db.add(row)
    # F2-5: transactional outbox — same flush, caller commits together (best-effort if table missing)
    outbox_added = False
    out = None
    try:
        from procurement_platform.persistence.models import OutboxEvent

        out = OutboxEvent(
            event_id=new_id("out"),
            aggregate_id=execution_id,
            event_type=f"outbox:{event_type}",
            payload=event.model_dump(mode="json"),
            created_at=utcnow(),
            processed_at=None,
            attempts=0,
            last_error=None,
        )
        db.add(out)
        outbox_added = True
    except Exception:
        outbox_added = False
    # flush but not commit — caller decides; handle missing table gracefully
    try:
        db.flush()
    except Exception as e:
        # if outbox table missing, remove pending out and retry audit only
        if outbox_added and out is not None:
            try:
                db.expunge(out)  # type: ignore[arg-type]
            except Exception:
                pass
            # try flush again with only audit row
            try:
                db.flush()
            except Exception:
                raise
        else:
            raise
    return event
