"""
Platform approvals — generic human-in-the-loop (Fase 11).

Snapshot inmutable, scope_hash, expiración 24h, doble aprobación, SLA 12h,
delegación, locks, audit. Reusable for procurement and expense.

No domain import at top level; domains call create_approval_request with
their own proposal Snapshot.
"""

from __future__ import annotations

from typing import Any

# Lazy re-export
def get_approval_service():
    from procurement_platform.approvals import service as _svc

    return _svc


__all__ = ["get_approval_service"]
