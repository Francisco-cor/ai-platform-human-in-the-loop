"""Feedback loop F4-5 — thumbs up/down per chunk, boosting retrieval.

Stores feedback_score in DocumentChunkRow (feedback_score / feedback_count) and
mirrors to in-memory RetrievalService for boosting (hybrid 0.05 per point).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session


def record_feedback(
    db: Session,
    *,
    chunk_id: str,
    useful: bool,
    actor_id: str = "user",
    tenant_id: str | None = None,
) -> dict[str, Any]:
    """Registra feedback útil/no útil y actualiza DocumentChunkRow.

    - useful=True => +1, False => -1
    - feedback_score acumula, feedback_count incrementa
    - Si chunk no existe en DB pero está en memoria, actualiza memoria igual (for tests sin DB)
    - Retorna dict con chunk_id, feedback_score, feedback_count
    """
    from procurement_platform.persistence.models import DocumentChunkRow

    row = db.get(DocumentChunkRow, chunk_id) if db is not None else None
    delta = 1 if useful else -1

    if row is not None:
        # tenant check if provided
        if tenant_id and row.tenant_id != tenant_id:
            raise ValueError(f"tenant mismatch: {tenant_id} != {row.tenant_id}")
        row.feedback_score = float((row.feedback_score or 0) + delta)
        row.feedback_count = int((row.feedback_count or 0) + 1)
        row.updated_at = datetime.now(UTC)
        db.add(row)
        try:
            db.commit()
            db.refresh(row)
        except Exception:
            try:
                db.rollback()
            except Exception:
                pass
            raise
        score = float(row.feedback_score)
        count = int(row.feedback_count)
    else:
        # no DB row — still give a result for in-memory boosting
        score = float(delta)
        count = 1

    # mirror to in-memory retrieval chunks
    try:
        from procurement_platform.workflows.orchestrator import get_rag_service

        rag = get_rag_service()
        if rag is not None:
            for ch in rag.retrieval._chunks:  # type: ignore
                if ch.metadata.chunk_id == chunk_id:
                    # set feedback_score on metadata (dynamic attr)
                    try:
                        ch.metadata.feedback_score = score
                    except Exception:
                        # if pydantic extra not allowed, store in __dict__
                        ch.metadata.__dict__["feedback_score"] = score
                    break
    except Exception:
        pass

    return {
        "chunk_id": chunk_id,
        "useful": useful,
        "feedback_score": score,
        "feedback_count": count,
        "actor_id": actor_id,
    }


def get_feedback_stats(db: Session, chunk_id: str) -> dict[str, Any] | None:
    from procurement_platform.persistence.models import DocumentChunkRow

    row = db.get(DocumentChunkRow, chunk_id) if db is not None else None
    if row is None:
        return None
    return {
        "chunk_id": row.chunk_id,
        "feedback_score": float(row.feedback_score or 0),
        "feedback_count": int(row.feedback_count or 0),
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def list_top_feedback(db: Session, tenant_id: str, limit: int = 10) -> list[dict[str, Any]]:
    from procurement_platform.persistence.models import DocumentChunkRow

    if db is None:
        return []
    rows = (
        db.query(DocumentChunkRow)
        .filter(DocumentChunkRow.tenant_id == tenant_id)
        .order_by(DocumentChunkRow.feedback_score.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "chunk_id": r.chunk_id,
            "document_id": r.document_id,
            "feedback_score": float(r.feedback_score or 0),
            "feedback_count": int(r.feedback_count or 0),
            "text_preview": (r.text[:120] if isinstance(r.text, str) else str(r.text)[:120]),
        }
        for r in rows
    ]
