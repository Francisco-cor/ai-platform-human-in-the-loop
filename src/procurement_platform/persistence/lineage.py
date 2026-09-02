"""Lineage queries — Fase 9 Data Platform.

Cada audit event incluye lineage: {document_ids, policy_ids, supplier_ids, execution_id}
Permite BigQuery vista procurement_lineage: SELECT * WHERE document_id=... → ejecuciones afectadas.

Para MVP, implementamos queries sobre audit_events (SQLite/PG) filtrando details->lineage.

Uso:
  from procurement_platform.persistence.lineage import get_executions_for_document, get_lineage_for_execution
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from procurement_platform.persistence.models import AuditEventRow


def _extract_lineage(row: AuditEventRow) -> dict:
    details = row.details or {}
    lineage = details.get("lineage") if isinstance(details, dict) else {}
    if not isinstance(lineage, dict):
        return {"document_ids": [], "policy_ids": [], "supplier_ids": []}
    return {
        "document_ids": lineage.get("document_ids", []),
        "policy_ids": lineage.get("policy_ids", []),
        "supplier_ids": lineage.get("supplier_ids", []),
        "execution_id": row.execution_id,
        "event_type": row.event_type,
        "timestamp": row.timestamp.isoformat() if row.timestamp else None,
    }


def get_executions_for_document(db: Session, document_id: str) -> list[dict]:
    """Retorna ejecuciones que usaron document_id (via lineage)."""
    rows = db.query(AuditEventRow).all()
    result = []
    seen = set()
    for r in rows:
        lineage = _extract_lineage(r)
        if document_id in lineage.get("document_ids", []):
            if r.execution_id not in seen:
                seen.add(r.execution_id)
                result.append(
                    {
                        "execution_id": r.execution_id,
                        "document_id": document_id,
                        "event_type": r.event_type,
                        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                        "lineage": lineage,
                    }
                )
    return result


def get_executions_for_policy(db: Session, policy_id: str) -> list[dict]:
    rows = db.query(AuditEventRow).all()
    result = []
    seen = set()
    for r in rows:
        lineage = _extract_lineage(r)
        if policy_id in lineage.get("policy_ids", []):
            if r.execution_id not in seen:
                seen.add(r.execution_id)
                result.append(
                    {
                        "execution_id": r.execution_id,
                        "policy_id": policy_id,
                        "event_type": r.event_type,
                        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                        "lineage": lineage,
                    }
                )
    return result


def get_executions_for_supplier(db: Session, supplier_id: str) -> list[dict]:
    rows = db.query(AuditEventRow).all()
    result = []
    seen = set()
    for r in rows:
        lineage = _extract_lineage(r)
        if supplier_id in lineage.get("supplier_ids", []):
            if r.execution_id not in seen:
                seen.add(r.execution_id)
                result.append(
                    {
                        "execution_id": r.execution_id,
                        "supplier_id": supplier_id,
                        "event_type": r.event_type,
                        "timestamp": r.timestamp.isoformat() if r.timestamp else None,
                        "lineage": lineage,
                    }
                )
    return result


def get_lineage_for_execution(db: Session, execution_id: str) -> dict:
    """Retorna lineage agregado para una ejecución."""
    rows = db.query(AuditEventRow).filter(AuditEventRow.execution_id == execution_id).all()
    doc_ids = set()
    pol_ids = set()
    sup_ids = set()
    for r in rows:
        lineage = _extract_lineage(r)
        doc_ids.update(lineage.get("document_ids", []))
        pol_ids.update(lineage.get("policy_ids", []))
        sup_ids.update(lineage.get("supplier_ids", []))
    return {
        "execution_id": execution_id,
        "document_ids": sorted(doc_ids),
        "policy_ids": sorted(pol_ids),
        "supplier_ids": sorted(sup_ids),
        "events_count": len(rows),
    }
