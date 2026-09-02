"""
Builtin tool example — Fase 11 plugin registry.

Tool: calculate_shortage
Handler signature: handler(payload: dict) -> dict
"""

from __future__ import annotations

from typing import Any

schema = {
    "type": "object",
    "properties": {
        "sku": {"type": "string"},
        "on_hand": {"type": "number"},
        "demand": {"type": "number"},
    },
    "required": ["sku", "on_hand", "demand"],
    "additionalProperties": False,
}


def handler(payload: dict[str, Any]) -> dict[str, Any]:
    sku = payload["sku"]
    on_hand = float(payload.get("on_hand", 0))
    demand = float(payload.get("demand", 0))
    shortage = max(0.0, demand - on_hand)
    return {"sku": sku, "shortage": shortage, "unit": "piece"}


# marker for entry_points discovery
__all__ = ["handler", "schema"]
