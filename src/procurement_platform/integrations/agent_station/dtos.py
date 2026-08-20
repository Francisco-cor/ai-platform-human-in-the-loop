"""DTOs externos versionados — boundary Agent Station (Fase 0).

Estos modelos son la única fuente de verdad para la comunicación externa.
No deben importarse modelos de `domain` aquí; la traducción es explícita en el cliente.
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Inbound: Agent Station → Platform
# ---------------------------------------------------------------------------
class AgentStationCreateExecutionDTO(BaseModel):
    """POST /v1/procurement/executions — contrato externo v1."""

    version: Literal["1.0"] = "1.0"
    request_id: str | None = None
    tenant_id: str = "tenant_demo"
    requester_id: str = "user_01"
    raw_intent: str | None = None
    items: list[dict[str, Any]] | None = None  # [{sku, quantity, unit}]
    horizon_days: int = 21
    location_id: str = "warehouse_north"
    currency: str = "USD"
    source: str = "agent_station"
    idempotency_key: str | None = None
    # propagación opcional
    traceparent: str | None = None


class AgentStationApprovalDecisionDTO(BaseModel):
    version: Literal["1.0"] = "1.0"
    decision: Literal["approved", "rejected", "needs_changes"]
    decided_by: str
    reason: str | None = None
    idempotency_key: str | None = None


# ---------------------------------------------------------------------------
# Outbound: Platform → Agent Station (callbacks)
# ---------------------------------------------------------------------------
class ExecutionUpdateCallbackDTO(BaseModel):
    version: Literal["1.0"] = "1.0"
    execution_id: str
    request_id: str
    tenant_id: str
    status: str
    current_node: str | None = None
    proposal_id: str | None = None
    approval_id: str | None = None
    scope_hash: str | None = None
    trace_id: str | None = None
    timestamp: datetime = Field(default_factory=utcnow)


class AgentStationErrorDTO(BaseModel):
    code: str
    message: str
    request_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Internal translation helpers (no domain import to keep boundary strict)
# ---------------------------------------------------------------------------
def to_internal_create_payload(dto: AgentStationCreateExecutionDTO) -> dict[str, Any]:
    return {
        "request_id": dto.request_id,
        "tenant_id": dto.tenant_id,
        "requester_id": dto.requester_id,
        "raw_intent": dto.raw_intent,
        "items": dto.items,
        "horizon_days": dto.horizon_days,
        "location_id": dto.location_id,
        "currency": dto.currency,
        "source": dto.source,
        "idempotency_key": dto.idempotency_key,
    }
