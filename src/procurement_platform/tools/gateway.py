"""Tool Gateway — Fase 4 (§8).

Frontera única para todas las llamadas a herramientas:
1. valida schema entrada
2. verifica identidad, tenant, permisos
3. comprueba allowlist por estado
4. comprueba budgets y rate limits
5. verifica aprobación si requiere
6. añade idempotency key
7. ejecuta con timeout
8. valida schema salida
9. publica eventos
10. redacta secretos

Para Fase 4 implementamos validación, allowlist, budgets, idempotencia y aprobación.
"""
from __future__ import annotations

import hashlib
import json
import time
from typing import Any


from procurement_platform.config.settings import get_settings
from procurement_platform.domain.models import ExecutionState
from procurement_platform.tools.definitions import TOOL_ALLOWLIST_BY_STATE, TOOL_SCHEMAS


class ToolGatewayError(RuntimeError):
    def __init__(self, code: str, message: str, details: dict | None = None) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.details = details or {}


class ToolBudget:
    def __init__(self, max_total_calls: int = 20, max_supplier_queries: int = 5, max_proposals: int = 3) -> None:
        self.max_total_calls = max_total_calls
        self.max_supplier_queries = max_supplier_queries
        self.max_proposals = max_proposals
        self.total_calls = 0
        self.supplier_queries = 0
        self.proposals = 0

    def check_and_increment(self, tool_name: str) -> None:
        self.total_calls += 1
        if self.total_calls > self.max_total_calls:
            raise ToolGatewayError("budget_exceeded", f"max_total_calls {self.max_total_calls} excedido", {"tool": tool_name})
        if tool_name == "search_suppliers":
            self.supplier_queries += 1
            if self.supplier_queries > self.max_supplier_queries:
                raise ToolGatewayError("budget_exceeded", f"max_supplier_queries {self.max_supplier_queries} excedido")
        if tool_name in ("create_draft_purchase_order", "submit_purchase_order"):
            self.proposals += 1
            if self.proposals > self.max_proposals:
                raise ToolGatewayError("budget_exceeded", f"max_proposals {self.max_proposals} excedido")

    def to_dict(self) -> dict:
        return {"total_calls": self.total_calls, "supplier_queries": self.supplier_queries, "proposals": self.proposals}


class ToolGateway:
    """Gateway síncrono — Fase 4."""

    def __init__(self, budget: ToolBudget | None = None) -> None:
        self.budget = budget or ToolBudget(
            max_total_calls=get_settings().max_tool_calls_per_execution,
            max_supplier_queries=get_settings().max_supplier_queries_per_execution,
            max_proposals=get_settings().max_proposals_per_execution,
        )
        # idempotency store (en producción Redis + Postgres)
        self._idempotency: dict[str, Any] = {}
        # para tests: registro de llamadas
        self.call_log: list[dict[str, Any]] = []

    def _validate_schema(self, tool_name: str, payload: Any, direction: str = "input") -> None:
        schema = TOOL_SCHEMAS.get(tool_name)
        if not schema:
            raise ToolGatewayError("unknown_tool", f"herramienta desconocida: {tool_name}")
        schema_part = schema.get(direction)
        if not schema_part:
            return
        # Si el payload es lista (output de algunas tools), validar que sea lista y no chequear required
        if isinstance(payload, list):
            if schema_part.get("type") == "array":
                return
            # si se esperaba objeto pero se recibió lista, error
            raise ToolGatewayError("validation_error", f"output de {tool_name} debe ser objeto, se recibió lista")
        # ahora payload es dict
        required = schema_part.get("required", [])
        for field in required:
            if field not in payload:
                raise ToolGatewayError("validation_error", f"campo requerido '{field}' faltante para {tool_name}", {"field": field})
        props = schema_part.get("properties", {})
        for k, v in payload.items():
            if k in props and "type" in props[k]:
                expected = props[k]["type"]
                if expected == "string" and not isinstance(v, str):
                    raise ToolGatewayError("validation_error", f"campo {k} debe ser string")
                if expected == "number" and not isinstance(v, (int, float)):
                    raise ToolGatewayError("validation_error", f"campo {k} debe ser number")
                if expected == "integer" and not isinstance(v, int):
                    raise ToolGatewayError("validation_error", f"campo {k} debe ser integer")
                if expected == "array" and not isinstance(v, list):
                    raise ToolGatewayError("validation_error", f"campo {k} debe ser array")

    def _check_allowlist(self, tool_name: str, state: ExecutionState) -> None:
        allowed = TOOL_ALLOWLIST_BY_STATE.get(state.value, set())
        # si el estado no está en el allowlist, usar default: solo read tools?
        if state.value not in TOOL_ALLOWLIST_BY_STATE:
            allowed = set()
        if tool_name not in allowed and state != ExecutionState.POLICY_CHECKED:
            # POLICY_CHECKED permite submit con aprobación, pero gateway verifica after
            if tool_name not in allowed:
                raise ToolGatewayError("not_allowed_for_state", f"herramienta {tool_name} no permitida en estado {state.value}", {"state": state.value})

    def _check_approval(self, tool_name: str, has_approval: bool) -> None:
        schema = TOOL_SCHEMAS.get(tool_name, {})
        if schema.get("requires_approval") and not has_approval:
            raise ToolGatewayError("approval_required", f"herramienta {tool_name} requiere aprobación humana vigente")

    def _idempotency_key(self, execution_id: str, tool_name: str, payload: dict) -> str:
        raw = json.dumps({"execution_id": execution_id, "tool": tool_name, "payload": payload}, sort_keys=True)
        return "idem_" + hashlib.sha256(raw.encode()).hexdigest()[:16]

    def call(
        self,
        *,
        tool_name: str,
        payload: dict,
        execution_id: str,
        state: ExecutionState,
        actor_id: str = "agent",
        tenant_id: str = "tenant_demo",
        has_approval: bool = False,
        idempotency_key: str | None = None,
        timeout_ms: int = 5000,
    ) -> dict[str, Any]:
        t0 = time.time()
        # 1. valida input
        self._validate_schema(tool_name, payload, "input")
        # 2. verifica tenant (stub: solo verifica no vacío)
        if not tenant_id:
            raise ToolGatewayError("unauthorized", "tenant_id requerido")
        # 3. allowlist
        self._check_allowlist(tool_name, state)
        # 4. budgets
        self.budget.check_and_increment(tool_name)
        # 5. aprobación
        self._check_approval(tool_name, has_approval)
        # 6. idempotencia
        key = idempotency_key or self._idempotency_key(execution_id, tool_name, payload)
        if key in self._idempotency:
            # replay idempotente
            return self._idempotency[key]

        # 7. ejecución simulada (Fase 4: tools simuladas, no efectos reales externos)
        # En prod, aquí se llamaría al handler real con timeout
        result = self._execute_simulated(tool_name, payload, execution_id)

        # 8. valida salida
        self._validate_schema(tool_name, result, "output")

        # 9. publicar evento (audit) — se hace fuera del gateway, pero logueamos
        latency_ms = int((time.time() - t0) * 1000)
        self.call_log.append(
            {
                "tool": tool_name,
                "execution_id": execution_id,
                "state": state.value,
                "payload": payload,
                "result": result,
                "latency_ms": latency_ms,
                "idempotency_key": key,
                "actor_id": actor_id,
            }
        )
        # 10. redactar secretos (no aplica en simulación)

        self._idempotency[key] = result
        return result

    def _execute_simulated(self, tool_name: str, payload: dict, execution_id: str) -> dict:
        # Simulación determinista para Fase 4
        if tool_name == "get_inventory":
            return {"on_hand": 20, "reserved": 5, "in_transit": 15}
        if tool_name == "get_open_purchase_orders":
            return [{"order_id": "po_001", "sku": payload.get("sku", "MAT-001"), "quantity": 15}]
        if tool_name == "retrieve_policy":
            return [{"policy_id": "budget_limit", "version": "1.0.0", "content": "límite 5000"}]
        if tool_name == "search_suppliers":
            return [{"supplier_id": "supplier_demo", "unit_price": 10.0, "currency": "USD"}]
        if tool_name == "calculate_shortage":
            return [{"sku": "MAT-001", "shortage": 138}]
        if tool_name == "create_draft_purchase_order":
            return {"draft_id": f"draft_{execution_id[:8]}"}
        if tool_name == "submit_purchase_order":
            return {"order_id": f"order_{execution_id[:8]}", "status": "submitted"}
        if tool_name == "cancel_draft_purchase_order":
            return {"status": "cancelled"}
        return {"status": "ok"}

    def reset(self) -> None:
        self._idempotency.clear()
        self.call_log.clear()
        self.budget = ToolBudget()
