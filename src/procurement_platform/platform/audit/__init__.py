"""
Platform audit — generic append-only event bus (Fase 11).

Every execution emits audit events with trace_id, hashes, lineage, cost.
Outbox → BigQuery/GCS drainer is generic.

No domain import.
"""

from __future__ import annotations

from procurement_platform.audit.service import create_audit_event, hash_payload

__all__ = ["create_audit_event", "hash_payload"]
