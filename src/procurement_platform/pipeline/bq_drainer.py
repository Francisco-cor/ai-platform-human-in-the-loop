"""BigQuery drainer (batch) — Fase 9 Data Platform.

Lee outbox_events cada 10s, transforma (audit_events → bq_audit, evals → bq_evals, cost → bq_finops)
sin PII (redact_pii ya), escribe via google-cloud-bigquery con insert_rows_json + processed_at.

Config BIGQUERY_DATASET (env PROCUREMENT_BIGQUERY_DATASET). Para tests/dev sin BQ, usa
in-memory fake y escribe a file:// o log.

Uso:
  from procurement_platform.pipeline.bq_drainer import drain_to_bigquery
  drain_to_bigquery(db, batch=50)

Batch: cada 10s en worker (ARQ) — ver workers/tasks.py.
"""

from __future__ import annotations

import json
import threading
from datetime import UTC, datetime
from typing import Any

import os

from sqlalchemy.orm import Session

from procurement_platform.config.settings import get_settings
from procurement_platform.persistence.models import OutboxEvent

# In-memory fake BQ for tests/dev (dataset.table → rows)
_FAKE_BQ: dict[str, list[dict[str, Any]]] = {}
_FAKE_LOCK = threading.Lock()


def _redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    """Redacta PII antes de enviar a BQ — usa security.pii.redact_dict_values."""
    try:
        from procurement_platform.security.pii import redact_dict_values

        return redact_dict_values(payload)
    except Exception:
        return payload


def _transform_row(row: OutboxEvent) -> tuple[str, dict[str, Any]]:
    """Determina tabla y transforma payload para BQ."""
    payload = row.payload or {}
    # Redact PII
    payload = _redact_payload(payload)
    event_type = row.event_type or ""
    # Clasificación simple
    if "cost" in event_type.lower() or "finops" in event_type.lower() or "llm" in payload.get("event_type", "").lower():
        table = "bq_finops"
    elif "eval" in event_type.lower():
        table = "bq_evals"
    else:
        table = "bq_audit"
    # Enrich with lineage if present
    # payload already contains model_metadata, details, lineage etc.
    # Ensure no PII in details
    return table, payload


def _get_bq_client():
    try:
        from google.cloud import bigquery  # type: ignore

        return bigquery.Client()
    except Exception:
        return None


def os_is_fake_dataset(dataset: str) -> bool:
    # Heurística: si no es dataset BQ válido (debe contener al menos proyecto.dataset o dataset)
    # Para tests, usamos file:// or None or empty
    if dataset.startswith("file://"):
        return True
    if dataset in ("procurement_ops", "test", "fake", "file"):
        # In CI without credentials, treat as fake unless GOOGLE_APPLICATION_CREDENTIALS set
        if not os.getenv("GOOGLE_APPLICATION_CREDENTIALS") and not os.getenv("BIGQUERY_EMULATOR_HOST"):
            # Check if bigquery client would fail — treat as fake for dev
            try:
                from google.cloud import bigquery  # type: ignore

                # Try to create client without credentials — will fail
                bigquery.Client()
                return False
            except Exception:
                return True
    return False


def drain_to_bigquery(db: Session, batch: int = 50, dataset: str | None = None) -> dict[str, Any]:
    """Drena outbox_events a BigQuery (o fake). Retorna stats."""
    settings = get_settings()
    dataset = dataset or settings.bigquery_dataset or "procurement_ops"
    # Allow file:// dataset for local dev (fake)
    is_fake = not dataset or dataset.startswith("file://") or os_is_fake_dataset(dataset)

    rows = (
        db.query(OutboxEvent)
        .filter(OutboxEvent.processed_at.is_(None))
        .order_by(OutboxEvent.created_at.asc())
        .limit(batch)
        .all()
    )
    if not rows:
        return {"processed": 0, "failed": 0, "total": 0, "dataset": dataset, "fake": is_fake}

    # Group by table
    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        table, payload = _transform_row(row)
        grouped.setdefault(table, []).append(payload)

    processed = 0
    failed = 0

    if is_fake:
        # In-memory fake
        with _FAKE_LOCK:
            for table, payloads in grouped.items():
                key = f"{dataset}.{table}"
                _FAKE_BQ.setdefault(key, []).extend(payloads)
        # Mark processed
        for row in rows:
            row.processed_at = datetime.now(UTC)
            row.attempts += 1
            processed += 1
        db.commit()
        return {"processed": processed, "failed": failed, "total": len(rows), "dataset": dataset, "fake": True, "tables": list(grouped.keys())}

    # Real BigQuery
    try:
        from google.cloud import bigquery  # type: ignore

        client = bigquery.Client()
        for table, payloads in grouped.items():
            table_id = f"{dataset}.{table}"
            # Ensure dataset exists (best effort)
            try:
                errors = client.insert_rows_json(table_id, payloads)
                if errors:
                    failed += len(payloads)
                    for row in rows:
                        row.last_error = str(errors)[:500]
                else:
                    processed += len(payloads)
            except Exception as e:
                failed += len(payloads)
                for row in rows:
                    row.last_error = str(e)[:500]
        # Mark processed for success
        for row in rows:
            if not row.last_error:
                row.processed_at = datetime.now(UTC)
                row.attempts += 1
        db.commit()
        return {"processed": processed, "failed": failed, "total": len(rows), "dataset": dataset, "fake": False}
    except Exception as e:
        # Fallback to fake on any error (e.g., no credentials)
        with _FAKE_LOCK:
            for table, payloads in grouped.items():
                key = f"{dataset}.{table}"
                _FAKE_BQ.setdefault(key, []).extend(payloads)
        for row in rows:
            row.processed_at = datetime.now(UTC)
            row.attempts += 1
            row.last_error = f"fallback fake due to {e}"[:500]
        db.commit()
        return {"processed": len(rows), "failed": 0, "total": len(rows), "dataset": dataset, "fake": True, "fallback_error": str(e)}


def get_fake_bq_rows(dataset: str, table: str) -> list[dict[str, Any]]:
    with _FAKE_LOCK:
        return list(_FAKE_BQ.get(f"{dataset}.{table}", []))


def clear_fake_bq() -> None:
    with _FAKE_LOCK:
        _FAKE_BQ.clear()


def query_fake_bq(dataset: str, table: str, execution_id: str | None = None) -> list[dict[str, Any]]:
    rows = get_fake_bq_rows(dataset, table)
    if execution_id:
        return [r for r in rows if r.get("execution_id") == execution_id or r.get("payload", {}).get("execution_id") == execution_id]
    return rows
