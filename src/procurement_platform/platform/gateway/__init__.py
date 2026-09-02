"""
Platform gateway — generic tool boundary (Fase 11).

Validates, authorizes, budgets, rate-limits, idempotency, audit for any tool.
Domain-specific allowlists and schemas live in tools/definitions.py (procurement)
and domains/expense/tools.py (expense) but gateway core is generic.

Usage:
  from procurement_platform.platform.gateway import get_gateway
  gw = get_gateway()
  gw.call(tool_name, payload, execution_id, state, tenant_id)
"""

from __future__ import annotations

from typing import Any

# Lazy import to avoid importing domain at import time
_gateway = None


def get_gateway():
    global _gateway
    if _gateway is None:
        from procurement_platform.tools.gateway import ToolGateway

        _gateway = ToolGateway()
    return _gateway


__all__ = ["get_gateway"]
