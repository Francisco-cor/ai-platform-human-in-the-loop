"""
Expense policy engine — deterministic (Fase 11).

Policies:
- amount_positive: amount >0
- currency_supported: USD only (MVP)
- budget_limit: amount <= 2000 (delegated limit)
- risk: high if amount >1000 (requires 2 approvals)
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class ExpensePolicyConfig:
    budget_limit: float = 2000.0
    delegated_limit: float = 1000.0
    supported_currencies: tuple[str, ...] = ("USD",)


@dataclass
class PolicyCheck:
    policy_id: str
    decision: str  # pass|fail|needs_review
    reason: str
    blocking: bool
    facts: dict[str, Any]


def run_expense_policy_checks(
    amount: float,
    currency: str,
    reason: str,
    config: ExpensePolicyConfig | None = None,
) -> list[PolicyCheck]:
    cfg = config or ExpensePolicyConfig()
    checks: list[PolicyCheck] = []

    # amount_positive
    if amount <= 0:
        checks.append(PolicyCheck("amount_positive", "fail", "Amount must be >0", True, {"amount": amount}))
    else:
        checks.append(PolicyCheck("amount_positive", "pass", "Amount OK", False, {"amount": amount}))

    # currency
    if currency not in cfg.supported_currencies:
        checks.append(PolicyCheck("currency_supported", "fail", f"Currency {currency} not supported", True, {"currency": currency}))
    else:
        checks.append(PolicyCheck("currency_supported", "pass", "Currency OK", False, {"currency": currency}))

    # budget
    if amount > cfg.budget_limit:
        checks.append(PolicyCheck("budget_limit", "fail", f"Amount {amount} > budget {cfg.budget_limit}", True, {"amount": amount, "limit": cfg.budget_limit}))
    else:
        checks.append(PolicyCheck("budget_limit", "pass", "Within budget", False, {"amount": amount, "limit": cfg.budget_limit}))

    # risk
    if amount > cfg.delegated_limit:
        checks.append(PolicyCheck("risk_high", "needs_review", "Amount exceeds delegated limit, requires 2 approvals", False, {"amount": amount, "delegated": cfg.delegated_limit}))
    else:
        checks.append(PolicyCheck("risk_low", "pass", "Low risk", False, {"amount": amount}))

    return checks


def is_blocked(checks: list[PolicyCheck]) -> bool:
    return any(c.blocking and c.decision == "fail" for c in checks)
