"""Definiciones de herramientas — Fase 4 (§8).

Cada herramienta tiene input_schema y output_schema (JSON Schema) y metadata.
"""
from __future__ import annotations

from typing import Any

# Schemas simplificados (JSON Schema draft 7)
TOOL_SCHEMAS: dict[str, dict[str, Any]] = {
    "get_inventory": {
        "input": {
            "type": "object",
            "required": ["sku", "location_id"],
            "properties": {"sku": {"type": "string"}, "location_id": {"type": "string"}, "tenant_id": {"type": "string"}},
        },
        "output": {"type": "object", "properties": {"on_hand": {"type": "number"}, "reserved": {"type": "number"}, "in_transit": {"type": "number"}}},
        "effect": "read",
        "requires_approval": False,
    },
    "get_open_purchase_orders": {
        "input": {"type": "object", "properties": {"sku": {"type": "string"}, "location_id": {"type": "string"}}},
        "output": {"type": "array", "items": {"type": "object"}},
        "effect": "read",
        "requires_approval": False,
    },
    "retrieve_policy": {
        "input": {"type": "object", "required": ["query"], "properties": {"query": {"type": "string"}, "tenant_id": {"type": "string"}}},
        "output": {"type": "array", "items": {"type": "object"}},
        "effect": "read",
        "requires_approval": False,
    },
    "search_suppliers": {
        "input": {"type": "object", "required": ["sku", "quantity"], "properties": {"sku": {"type": "string"}, "quantity": {"type": "number"}, "currency": {"type": "string"}}},
        "output": {"type": "array", "items": {"type": "object"}},
        "effect": "read",
        "requires_approval": False,
    },
    "calculate_shortage": {
        "input": {"type": "object", "required": ["items", "location_id", "horizon_days"], "properties": {"items": {"type": "array"}, "location_id": {"type": "string"}, "horizon_days": {"type": "integer"}}},
        "output": {"type": "array", "items": {"type": "object"}},
        "effect": "read",
        "requires_approval": False,
    },
    "create_draft_purchase_order": {
        "input": {"type": "object", "required": ["supplier_id", "lines"], "properties": {"supplier_id": {"type": "string"}, "lines": {"type": "array"}}},
        "output": {"type": "object", "properties": {"draft_id": {"type": "string"}}},
        "effect": "write_reversible",
        "requires_approval": False,  # puede requerir según total/riesgo, lo decide policy engine
    },
    "submit_purchase_order": {
        "input": {"type": "object", "required": ["proposal_id"], "properties": {"proposal_id": {"type": "string"}}},
        "output": {"type": "object", "properties": {"order_id": {"type": "string"}, "status": {"type": "string"}}},
        "effect": "write_commit",
        "requires_approval": True,
    },
    "cancel_draft_purchase_order": {
        "input": {"type": "object", "required": ["draft_id"], "properties": {"draft_id": {"type": "string"}}},
        "output": {"type": "object", "properties": {"status": {"type": "string"}}},
        "effect": "write_reversible",
        "requires_approval": False,
    },
}

# Allowlist por estado del workflow (§8 gateway: herramienta válida para estado actual)
TOOL_ALLOWLIST_BY_STATE: dict[str, set[str]] = {
    "RECEIVED": {"get_inventory", "retrieve_policy"},
    "NORMALIZED": {"get_inventory", "retrieve_policy", "calculate_shortage"},
    "CONTEXT_LOADED": {"retrieve_policy", "calculate_shortage", "search_suppliers"},
    "POLICY_RETRIEVED": {"calculate_shortage", "search_suppliers", "get_inventory"},
    "SHORTAGE_CALCULATED": {"search_suppliers", "calculate_shortage", "retrieve_policy"},
    "SUPPLIERS_QUERIED": {"search_suppliers", "create_draft_purchase_order", "calculate_shortage"},
    "PROPOSAL_DRAFTED": {"create_draft_purchase_order", "retrieve_policy"},
    "POLICY_CHECKED": {"create_draft_purchase_order", "submit_purchase_order"},
    "AWAITING_APPROVAL": set(),  # ninguna herramienta automática, solo humana
    "APPROVED": {"submit_purchase_order"},
    # resto bloquea
}

# Presupuesto por defecto (Fase 4)
DEFAULT_BUDGETS: dict[str, int] = {
    "max_total_calls": 20,
    "max_supplier_queries": 5,
    "max_proposals": 3,
    "max_retries_per_tool": 2,
}
