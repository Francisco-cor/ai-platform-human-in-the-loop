"""
Platform workflow engine — generic durable runtime (Fase 11).

Generic primitives for any domain (procurement, expense):
- ExecutionState enum and transition validation
- Checkpoint durable (PG + JSON)
- Idempotency and locks (via infra/locks)
- Audit correlation (trace_id)
- Human approval pause/resume

Procurement-specific graph (14 nodos) lives in workflows/graph.py and
workflows/orchestrator.py; expense graph (8 nodos) lives in domains/expense.

This module re-exports generic helpers without importing domain-specific
inventory/suppliers so `import platform.workflow` does not require procurement.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from typing import Any

# Re-export generic models without importing domain inventory
from procurement_platform.domain.models import ExecutionState, is_valid_transition, new_id, utcnow


def compute_scope_hash(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


class WorkflowEngine:
    """Generic durable engine — domain graphs delegate here."""

    def __init__(self, domain: str):
        self.domain = domain
        self._lock = threading.Lock()

    def validate_transition(self, current: ExecutionState, target: ExecutionState) -> bool:
        return is_valid_transition(current, target)

    def checkpoint(self, execution_id: str, node: str, state: dict) -> dict:
        return {
            "execution_id": execution_id,
            "node": node,
            "state": state,
            "timestamp": utcnow().isoformat(),
            "domain": self.domain,
        }

    def get_info(self) -> dict:
        return {"domain": self.domain, "engine": "generic durable"}


__all__ = ["ExecutionState", "is_valid_transition", "new_id", "utcnow", "compute_scope_hash", "WorkflowEngine"]
