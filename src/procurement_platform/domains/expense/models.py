"""
Expense domain models — Fase 11 (second workflow).

Minimal, reuses platform core. All amounts in USD, total = amount (no lines).
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field


class ExpenseRequest(BaseModel):
    request_id: str
    tenant_id: str
    requester_id: str
    amount: float = Field(gt=0, description="Expense amount")
    currency: str = Field(default="USD", pattern="^[A-Z]{3}$")
    reason: str = Field(min_length=3, max_length=500)
    location_id: str | None = None
    created_at: datetime
    raw_intent: str | None = None

    model_config = {"extra": "ignore"}


class ExpenseProposal(BaseModel):
    proposal_id: str
    request_id: str
    execution_id: str
    amount: float
    currency: str = "USD"
    reason: str
    requester_id: str
    tenant_id: str
    risk_level: Literal["low", "medium", "high"] = "low"
    requires_human_approval: bool = True
    total: float
    confidence: float = 0.9
    policies_applied: list[str] = Field(default_factory=list)
    evidence: str = ""
    created_at: datetime

    model_config = {"extra": "ignore"}


class ExpenseApprovalRequest(BaseModel):
    approval_id: str
    proposal_id: str
    execution_id: str
    request_id: str
    tenant_id: str
    scope_hash: str
    proposal_snapshot: dict
    amount: float
    currency: str
    risk_level: str
    required_approvals: int = 1
    approvals_received: int = 0
    approvers: list[str] = Field(default_factory=list)
    status: Literal["pending", "approved", "rejected", "expired"] = "pending"
    requested_by: str
    requested_at: datetime
    expires_at: datetime
    decided_by: str | None = None
    decision_reason: str | None = None
    decided_at: datetime | None = None

    model_config = {"extra": "ignore"}
