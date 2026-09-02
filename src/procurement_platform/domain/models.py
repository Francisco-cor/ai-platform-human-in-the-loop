"""Domain contracts — Pydantic models versioned (Fase 1).

Garantías del Plan §6:
- Solicitud normalizada, Propuesta, Decisión de aprobación, Eventos de auditoría.
- Estados del workflow §5.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field, field_validator


def utcnow() -> datetime:
    return datetime.now(UTC)


def new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:12]}"


# ---------------------------------------------------------------------------
# ExecutionState — §5
# ---------------------------------------------------------------------------
class ExecutionState(str, Enum):
    RECEIVED = "RECEIVED"
    NORMALIZED = "NORMALIZED"
    CONTEXT_LOADED = "CONTEXT_LOADED"
    POLICY_RETRIEVED = "POLICY_RETRIEVED"
    SHORTAGE_CALCULATED = "SHORTAGE_CALCULATED"
    SUPPLIERS_QUERIED = "SUPPLIERS_QUERIED"
    PROPOSAL_DRAFTED = "PROPOSAL_DRAFTED"
    POLICY_CHECKED = "POLICY_CHECKED"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"
    ACTION_EXECUTED = "ACTION_EXECUTED"
    VERIFIED = "VERIFIED"
    COMPLETED = "COMPLETED"
    NEEDS_CLARIFICATION = "NEEDS_CLARIFICATION"
    BLOCKED = "BLOCKED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_TERMINAL = "FAILED_TERMINAL"


# Valid transitions (simplified Fase 1 — linear happy path + terminal branches)
# Cualquier estado puede terminar en NEEDS_CLARIFICATION, BLOCKED, FAILED_*
TERMINAL_BRANCHES = {
    ExecutionState.NEEDS_CLARIFICATION,
    ExecutionState.BLOCKED,
    ExecutionState.FAILED_RETRYABLE,
    ExecutionState.FAILED_TERMINAL,
}

LINEAR_ORDER = [
    ExecutionState.RECEIVED,
    ExecutionState.NORMALIZED,
    ExecutionState.CONTEXT_LOADED,
    ExecutionState.POLICY_RETRIEVED,
    ExecutionState.SHORTAGE_CALCULATED,
    ExecutionState.SUPPLIERS_QUERIED,
    ExecutionState.PROPOSAL_DRAFTED,
    ExecutionState.POLICY_CHECKED,
    ExecutionState.AWAITING_APPROVAL,
    ExecutionState.APPROVED,
    ExecutionState.ACTION_EXECUTED,
    ExecutionState.VERIFIED,
    ExecutionState.COMPLETED,
]

VALID_TRANSITIONS: dict[ExecutionState, set[ExecutionState]] = {}

for i, state in enumerate(LINEAR_ORDER):
    nxt: set[ExecutionState] = set()
    if i + 1 < len(LINEAR_ORDER):
        nxt.add(LINEAR_ORDER[i + 1])
    # branches
    nxt |= TERMINAL_BRANCHES
    # AWAITING_APPROVAL can go to REJECTED / EXPIRED
    if state == ExecutionState.AWAITING_APPROVAL:
        nxt |= {ExecutionState.REJECTED, ExecutionState.EXPIRED}
    VALID_TRANSITIONS[state] = nxt

# REJECTED, EXPIRED, etc. can also go to terminal branches or stay terminal
for terminal in [
    ExecutionState.REJECTED,
    ExecutionState.EXPIRED,
    ExecutionState.COMPLETED,
    ExecutionState.BLOCKED,
    ExecutionState.FAILED_RETRYABLE,
    ExecutionState.FAILED_TERMINAL,
    ExecutionState.NEEDS_CLARIFICATION,
]:
    if terminal not in VALID_TRANSITIONS:
        VALID_TRANSITIONS[terminal] = set()


def is_valid_transition(from_state: ExecutionState, to_state: ExecutionState) -> bool:
    if to_state in TERMINAL_BRANCHES:
        return True
    return to_state in VALID_TRANSITIONS.get(from_state, set())


# ---------------------------------------------------------------------------
# NormalizedRequest — §6
# ---------------------------------------------------------------------------
class RequestItem(BaseModel):
    sku: str = Field(..., min_length=1, max_length=64, description="SKU, e.g. MAT-001")
    quantity: float = Field(..., gt=0, description="Cantidad solicitada")
    unit: str = Field(default="piece", max_length=32)


class NormalizedRequest(BaseModel):
    request_id: str = Field(..., description="req_...")
    tenant_id: str
    requester_id: str
    items: list[RequestItem] = Field(..., min_length=1)
    horizon_days: int = Field(default=21, ge=1, le=365)
    location_id: str
    currency: str = Field(default="USD", min_length=3, max_length=3)
    source: str = Field(default="agent_station")
    created_at: datetime = Field(default_factory=utcnow)
    raw_intent: str | None = Field(default=None, description="Intent natural original si aplica")
    idempotency_key: str | None = None

    @field_validator("currency")
    @classmethod
    def uppercase_currency(cls, v: str) -> str:
        return v.upper()


class CreateExecutionRequest(BaseModel):
    """Payload POST /v1/procurement/executions — desde Agent Station o cliente."""

    request_id: str | None = Field(default=None, description="Si no se provee, se genera")
    tenant_id: str = Field(default="tenant_demo")
    requester_id: str = Field(default="user_01")
    raw_intent: str | None = Field(
        default="Necesitamos reponer materiales críticos para las próximas tres semanas."
    )
    items: list[RequestItem] | None = None
    horizon_days: int = Field(default=21, ge=1, le=365)
    location_id: str = Field(default="warehouse_north")
    currency: str = Field(default="USD")
    source: str = Field(default="agent_station")
    idempotency_key: str | None = None

    @field_validator("currency")
    @classmethod
    def upper(cls, v: str) -> str:
        return v.upper()


# ---------------------------------------------------------------------------
# Proposal — §6
# ---------------------------------------------------------------------------
class ProposalLine(BaseModel):
    sku: str
    quantity: float
    unit: str = "piece"
    unit_price: float = Field(..., ge=0)
    currency: str = "USD"
    estimated_delivery: datetime | None = None


class Proposal(BaseModel):
    proposal_id: str
    request_id: str
    execution_id: str
    supplier_id: str
    supplier_name: str | None = None
    evidence: str | None = Field(default=None, description="Cómo fue seleccionado el proveedor")
    lines: list[ProposalLine] = Field(..., min_length=1)
    subtotal: float = Field(..., ge=0)
    tax: float = Field(default=0, ge=0)
    total: float = Field(..., ge=0)
    currency: str = "USD"
    confidence: float = Field(default=0.8, ge=0, le=1)
    policies_applied: list[str] = Field(default_factory=list)
    policy_versions: dict[str, str] = Field(default_factory=dict)
    assumptions: list[str] = Field(default_factory=list)
    missing_data: list[str] = Field(default_factory=list)
    risk_level: Literal["low", "medium", "high"] = "low"
    requires_human_approval: bool = True
    scope_hash: str = Field(..., description="sha256:... de campos inmutables")
    created_at: datetime = Field(default_factory=utcnow)

    @staticmethod
    def compute_scope_hash(
        *, proposal_id: str, supplier_id: str, lines: list[dict], total: float, currency: str
    ) -> str:
        payload = json.dumps(
            {
                "proposal_id": proposal_id,
                "supplier_id": supplier_id,
                "lines": lines,
                "total": total,
                "currency": currency,
            },
            sort_keys=True,
            separators=(",", ":"),
        )
        return "sha256:" + hashlib.sha256(payload.encode()).hexdigest()

    @staticmethod
    def compute_total(lines: list[ProposalLine], tax: float = 0) -> float:
        return round(sum(li.quantity * li.unit_price for li in lines) + tax, 2)


# ---------------------------------------------------------------------------
# ApprovalDecision — §6
# ---------------------------------------------------------------------------
class ApprovalStatus(str, Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"
    expired = "expired"
    needs_changes = "needs_changes"


class ApprovalRequest(BaseModel):
    approval_id: str
    proposal_id: str
    execution_id: str
    request_id: str
    status: ApprovalStatus = ApprovalStatus.pending
    scope_hash: str
    requested_by: str
    requested_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime
    decided_by: str | None = None
    decision_reason: str | None = None
    decided_at: datetime | None = None
    # Fase 5 — snapshot inmutable y trazabilidad
    proposal_snapshot: dict[str, Any] | None = Field(
        default=None,
        description="Snapshot inmutable de la propuesta al momento de solicitar aprobación",
    )
    risk_level: str | None = None
    total: float | None = None
    currency: str | None = None
    required_approvals: int = Field(
        default=1,
        ge=1,
        le=5,
        description="Número de aprobaciones requeridas (doble aprobación si riesgo alto)",
    )
    approvals_received: int = Field(default=0, ge=0)
    approvers: list[str] = Field(default_factory=list)
    # Fase 7 — SLA escalation & delegation
    escalated_to: str | None = Field(default=None, description="Usuario a quien se escaló tras 12h")
    escalated_at: datetime | None = Field(default=None)
    sla_age_hours: float | None = Field(default=None, description="Horas desde requested_at")
    delegated_from: str | None = Field(default=None, description="Si aprobación fue delegada")

    def is_expired(self, now: datetime | None = None) -> bool:
        now = now or utcnow()
        return now > self.expires_at

    def is_scope_valid(self, proposal: Proposal) -> bool:  # type: ignore[no-redef]
        return self.scope_hash == proposal.scope_hash

    def can_decide(self, now: datetime | None = None) -> tuple[bool, str]:
        if self.status != ApprovalStatus.pending:
            return False, f"already_decided:{self.status.value}"
        if self.is_expired(now):
            return False, "expired"
        return True, "ok"


class ApprovalDecision(BaseModel):
    approval_id: str
    proposal_id: str
    status: Literal["approved", "rejected", "needs_changes"]
    decided_by: str
    decision_reason: str | None = None
    scope_hash: str | None = None
    created_at: datetime = Field(default_factory=utcnow)
    expires_at: datetime | None = None


# ---------------------------------------------------------------------------
# AuditEvent — §6
# ---------------------------------------------------------------------------
class AuditEvent(BaseModel):
    event_id: str
    execution_id: str
    request_id: str
    event_type: str = Field(..., examples=["execution.created", "tool_call.completed"])
    actor_type: Literal["agent", "human", "system"]
    actor_id: str
    tool_name: str | None = None
    input_hash: str | None = None
    output_hash: str | None = None
    policy_decisions: list[str] = Field(default_factory=list)
    model_metadata: dict[str, Any] | None = None
    timestamp: datetime = Field(default_factory=utcnow)
    trace_id: str | None = None
    details: dict[str, Any] = Field(default_factory=dict)


# ---------------------------------------------------------------------------
# Execution — aggregate
# ---------------------------------------------------------------------------
class Execution(BaseModel):
    execution_id: str
    request_id: str
    tenant_id: str
    status: ExecutionState
    current_node: str | None = None
    normalized_request: NormalizedRequest | None = None
    proposal: Proposal | None = None
    approval_request: ApprovalRequest | None = None
    created_at: datetime = Field(default_factory=utcnow)
    updated_at: datetime = Field(default_factory=utcnow)
    trace_id: str | None = None

    @property
    def is_terminal(self) -> bool:
        return self.status in {
            ExecutionState.COMPLETED,
            ExecutionState.REJECTED,
            ExecutionState.EXPIRED,
            ExecutionState.BLOCKED,
            ExecutionState.FAILED_TERMINAL,
            ExecutionState.NEEDS_CLARIFICATION,
        }
