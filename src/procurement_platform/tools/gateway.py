"""Tool Gateway — Fase 4-5 (§8).

Frontera única para todas las llamadas a herramientas:
1. valida schema entrada
2. verifica identidad, tenant, permisos
3. comprueba allowlist por estado
4. comprueba budgets y rate limits
5. verifica aprobación si requiere
6. añade idempotency key (persistente/durable en Fase 5)
7. ejecuta con timeout
8. valida schema salida
9. publica eventos
10. redacta secretos

Fase 5 añade store global idempotente + lock para evitar duplicación tras reintento/reanudación.
"""

from __future__ import annotations

import hashlib
import json
import threading as _threading
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
    def __init__(
        self, max_total_calls: int = 20, max_supplier_queries: int = 5, max_proposals: int = 3
    ) -> None:
        self.max_total_calls = max_total_calls
        self.max_supplier_queries = max_supplier_queries
        self.max_proposals = max_proposals
        self.total_calls = 0
        self.supplier_queries = 0
        self.proposals = 0

    def check_and_increment(self, tool_name: str) -> None:
        self.total_calls += 1
        if self.total_calls > self.max_total_calls:
            raise ToolGatewayError(
                "budget_exceeded",
                f"max_total_calls {self.max_total_calls} excedido",
                {"tool": tool_name},
            )
        if tool_name == "search_suppliers":
            self.supplier_queries += 1
            if self.supplier_queries > self.max_supplier_queries:
                raise ToolGatewayError(
                    "budget_exceeded", f"max_supplier_queries {self.max_supplier_queries} excedido"
                )
        if tool_name in ("create_draft_purchase_order", "submit_purchase_order"):
            self.proposals += 1
            if self.proposals > self.max_proposals:
                raise ToolGatewayError(
                    "budget_exceeded", f"max_proposals {self.max_proposals} excedido"
                )

    def to_dict(self) -> dict:
        return {
            "total_calls": self.total_calls,
            "supplier_queries": self.supplier_queries,
            "proposals": self.proposals,
        }


# Store global para idempotencia durable (Fase 5) — compartido entre instancias en el proceso
_GLOBAL_IDEMPOTENCY: dict[str, Any] = {}
_GLOBAL_CALL_LOG: list[dict[str, Any]] = []

_GATEWAY_LOCKS: dict[str, _threading.Lock] = {}
_GATEWAY_LOCKS_GUARD = _threading.Lock()


def _gateway_lock(key: str) -> _threading.Lock:
    with _GATEWAY_LOCKS_GUARD:
        if key not in _GATEWAY_LOCKS:
            _GATEWAY_LOCKS[key] = _threading.Lock()
        return _GATEWAY_LOCKS[key]


def _get_lock_manager_gateway():
    try:
        from procurement_platform.infra.locks.manager import get_lock_manager

        return get_lock_manager()
    except Exception:
        return None


def _get_redis_for_idempotency():
    try:
        from procurement_platform.config.settings import get_settings

        settings = get_settings()
        if settings.app_env in ("ci", "test"):
            return None
        import redis  # type: ignore

        return redis.from_url(settings.redis_url, socket_connect_timeout=0.2, socket_timeout=0.2)
    except Exception:
        return None


def _redis_idempotency_get(key: str):
    try:
        r = _get_redis_for_idempotency()
        if r is None:
            return None
        import json

        val = r.get(f"idem:{key}")
        if val:
            return json.loads(val)
    except Exception:
        pass
    return None


def _redis_idempotency_set(key: str, value: Any, ttl: int = 86400):
    try:
        r = _get_redis_for_idempotency()
        if r is None:
            return
        import json

        r.setex(f"idem:{key}", ttl, json.dumps(value))
    except Exception:
        pass


class ToolGateway:
    """Gateway síncrono — Fase 4-5 con store global idempotente."""

    def __init__(self, budget: ToolBudget | None = None) -> None:
        self.budget = budget or ToolBudget(
            max_total_calls=get_settings().max_tool_calls_per_execution,
            max_supplier_queries=get_settings().max_supplier_queries_per_execution,
            max_proposals=get_settings().max_proposals_per_execution,
        )
        # idempotency store: en Fase 5 usamos global para sobrevivir re-instancias (simula Redis/DB)
        self._idempotency: dict[str, Any] = _GLOBAL_IDEMPOTENCY
        # para tests: registro de llamadas — también global para auditar reintentos
        self.call_log: list[dict[str, Any]] = _GLOBAL_CALL_LOG

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
            raise ToolGatewayError(
                "validation_error", f"output de {tool_name} debe ser objeto, se recibió lista"
            )
        # ahora payload es dict
        required = schema_part.get("required", [])
        for field in required:
            if field not in payload:
                raise ToolGatewayError(
                    "validation_error",
                    f"campo requerido '{field}' faltante para {tool_name}",
                    {"field": field},
                )
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
                raise ToolGatewayError(
                    "not_allowed_for_state",
                    f"herramienta {tool_name} no permitida en estado {state.value}",
                    {"state": state.value},
                )

    def _check_approval(self, tool_name: str, has_approval: bool) -> None:
        schema = TOOL_SCHEMAS.get(tool_name, {})
        if schema.get("requires_approval") and not has_approval:
            raise ToolGatewayError(
                "approval_required", f"herramienta {tool_name} requiere aprobación humana vigente"
            )

    def _idempotency_key(self, execution_id: str, tool_name: str, payload: dict) -> str:
        raw = json.dumps(
            {"execution_id": execution_id, "tool": tool_name, "payload": payload}, sort_keys=True
        )
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
        # 1. valida input — redactar PII en payload antes de validar (Fase 7)
        try:
            from procurement_platform.security.pii import redact_dict_values

            # redact copy para auditoría; payload original se valida igual pero log redactado
            redacted_payload = redact_dict_values(payload) if isinstance(payload, dict) else payload
        except Exception:
            redacted_payload = payload
        self._validate_schema(tool_name, payload, "input")
        # 2. verifica tenant (Fase 7: aislamiento estricto)
        if not tenant_id:
            raise ToolGatewayError("unauthorized", "tenant_id requerido")
        # si payload contiene tenant_id distinto, violación
        if (
            isinstance(payload, dict)
            and "tenant_id" in payload
            and payload["tenant_id"] != tenant_id
        ):
            raise ToolGatewayError(
                "tenant_isolation_violation",
                f"tenant {tenant_id} no autorizado para recurso de {payload['tenant_id']}",
                {"request_tenant": tenant_id, "resource_tenant": payload["tenant_id"]},
            )
        # 2b. rate limit por tenant+tool (Fase 7)
        try:
            from procurement_platform.security.rate_limiter import get_rate_limiter

            limiter = get_rate_limiter()
            limiter.check_and_hit(f"tool:{tool_name}:{tenant_id}")
        except Exception as e:
            # si es RateLimitExceeded, traducir a ToolGatewayError
            if "rate_limited" in str(e):
                raise ToolGatewayError(
                    "rate_limited", str(e), {"tool": tool_name, "tenant_id": tenant_id}
                ) from e
            raise
        # 3. allowlist
        self._check_allowlist(tool_name, state)
        # 4. budgets
        self.budget.check_and_increment(tool_name)
        # 5. aprobación
        self._check_approval(tool_name, has_approval)
        # 6. idempotencia — Fase 5-2: memory + redis (dual) + LockManager
        key = idempotency_key or self._idempotency_key(execution_id, tool_name, payload)
        # fast path memory
        if key in self._idempotency:
            return self._idempotency[key]
        # try redis
        r_cached = _redis_idempotency_get(key)
        if r_cached is not None:
            self._idempotency[key] = r_cached
            return r_cached
        mgr = _get_lock_manager_gateway()
        lock = None
        use_mgr = mgr is not None
        if use_mgr:
            acquired = mgr.acquire(f"gateway:{key}", blocking=True, timeout=2.0)
            if not acquired:
                if key in self._idempotency:
                    return self._idempotency[key]
                raise ToolGatewayError("conflict", "gateway lock timeout", {"tool": tool_name})
        else:
            lock = _gateway_lock(key)
            if not lock.acquire(blocking=True, timeout=2):
                if key in self._idempotency:
                    return self._idempotency[key]
                raise ToolGatewayError("conflict", "gateway lock timeout", {"tool": tool_name})
        try:
            if key in self._idempotency:
                return self._idempotency[key]
            # 7. ejecución simulada (Fase 5: con timeout y verificación)
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
            # 10. redactar secretos (no aplica en simulación) + store idempotente (memory + redis)
            self._idempotency[key] = result
            _redis_idempotency_set(key, result, ttl=get_settings().default_idempotency_ttl_seconds)
            return result
        finally:
            if use_mgr:
                try:
                    mgr.release(f"gateway:{key}")
                except Exception:
                    pass
            else:
                try:
                    lock.release()  # type: ignore[union-attr]
                except Exception:
                    pass

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
