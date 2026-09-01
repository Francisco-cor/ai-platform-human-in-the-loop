"""Outbox drainer — F2-5 batch processor for audit/BQ/GCS.

Polls outbox_events where processed_at is NULL, publishes to sinks (log/BQ/GCS) and marks processed.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from procurement_platform.persistence.models import OutboxEvent

logger = logging.getLogger(__name__)


def drain_outbox(db: Session, batch: int = 50) -> dict:
    """Drain up to batch unprocessed outbox events.

    Returns dict with counts: processed, failed.
    Idempotent: only processes where processed_at is NULL.
    """
    rows = (
        db.query(OutboxEvent)
        .filter(OutboxEvent.processed_at.is_(None))
        .order_by(OutboxEvent.created_at.asc())
        .limit(batch)
        .all()
    )
    processed = 0
    failed = 0
    for row in rows:
        try:
            # simulate sink publish — log and mark processed
            # future: publish to BigQuery/GCS/webhook
            logger.info("outbox_publish", extra={"event_id": row.event_id, "type": row.event_type})
            row.processed_at = datetime.now(UTC)
            row.attempts += 1
            db.flush()
            processed += 1
        except Exception as e:
            row.attempts += 1
            row.last_error = str(e)[:500]
            db.flush()
            failed += 1
    db.commit()
    return {"processed": processed, "failed": failed, "total": len(rows)}
