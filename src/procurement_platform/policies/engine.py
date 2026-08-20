"""Policy engine determinista — Fase 2.

Reglas puras, versionadas, sin LLM.
Cada check retorna PolicyCheckResult con decision pass|fail|needs_review y blocking.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from procurement_platform.domain.inventory import SUPPORTED_CURRENCIES, SUPPORTED_UNITS
from procurement_platform.domain.models import Proposal, ProposalLine


class PolicyCheckResult(BaseModel):
    policy_check_id: str
    decision: Literal["pass", "fail", "needs_review"]
    policy_id: str
    policy_version: str = "1.0.0"
    facts: dict[str, Any] = Field(default_factory=dict)
    reason: str
    blocking: bool = False


# ---------------------------------------------------------------------------
# Config de políticas (inyectable, versionada)
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class PolicyConfig:
    # presupuesto delegado por tenant/location (simulado)
    budget_limits: dict[tuple[str, str], float]  # (tenant, location) -> limit
    allowed_currencies: set[str] = None  # type: ignore
    allowed_units: set[str] = None  # type: ignore
    # sku allowlist por tenant (None = todas)
    sku_allowlist: dict[str, set[str]] | None = None
    # supplier allowlist
    supplier_allowlist: dict[str, set[str]] | None = None

    def __post_init__(self):
        if self.allowed_currencies is None:
            object.__setattr__(self, "allowed_currencies", SUPPORTED_CURRENCIES)
        if self.allowed_units is None:
            object.__setattr__(self, "allowed_units", SUPPORTED_UNITS)


# ---------------------------------------------------------------------------
# Checks individuales (puros)
# ---------------------------------------------------------------------------
def check_quantity_non_negative(lines: list[ProposalLine]) -> PolicyCheckResult:
    for li in lines:
        if li.quantity <= 0:
            return PolicyCheckResult(
                policy_check_id="check_quantity_non_negative",
                decision="fail",
                policy_id="quantity_non_negative",
                facts={"sku": li.sku, "quantity": li.quantity},
                reason=f"cantidad no positiva para {li.sku}: {li.quantity}",
                blocking=True,
            )
    return PolicyCheckResult(
        policy_check_id="check_quantity_non_negative",
        decision="pass",
        policy_id="quantity_non_negative",
        facts={"lines": len(lines)},
        reason="todas las cantidades >0",
        blocking=False,
    )


def check_unit_supported(lines: list[ProposalLine], allowed_units: set[str]) -> PolicyCheckResult:
    for li in lines:
        if li.unit not in allowed_units:
            return PolicyCheckResult(
                policy_check_id="check_unit_supported",
                decision="fail",
                policy_id="unit_compatibility",
                facts={"sku": li.sku, "unit": li.unit},
                reason=f"unidad no soportada {li.unit}",
                blocking=True,
            )
    return PolicyCheckResult(
        policy_check_id="check_unit_supported",
        decision="pass",
        policy_id="unit_compatibility",
        facts={"units": list(allowed_units)},
        reason="unidades compatibles",
        blocking=False,
    )


def check_currency(
    lines: list[ProposalLine], proposal_currency: str, allowed: set[str]
) -> PolicyCheckResult:
    if proposal_currency not in allowed:
        return PolicyCheckResult(
            policy_check_id="check_currency",
            decision="fail",
            policy_id="currency_valid",
            facts={"currency": proposal_currency},
            reason=f"moneda no permitida {proposal_currency}",
            blocking=True,
        )
    for li in lines:
        if li.currency != proposal_currency:
            return PolicyCheckResult(
                policy_check_id="check_currency",
                decision="fail",
                policy_id="currency_valid",
                facts={
                    "sku": li.sku,
                    "line_currency": li.currency,
                    "proposal_currency": proposal_currency,
                },
                reason="moneda de línea distinta a propuesta",
                blocking=True,
            )
    return PolicyCheckResult(
        policy_check_id="check_currency",
        decision="pass",
        policy_id="currency_valid",
        facts={"currency": proposal_currency},
        reason="moneda válida",
        blocking=False,
    )


def check_supplier_active(supplier_id: str, active_suppliers: set[str]) -> PolicyCheckResult:
    if supplier_id not in active_suppliers:
        return PolicyCheckResult(
            policy_check_id="check_supplier_active",
            decision="fail",
            policy_id="supplier_active",
            facts={"supplier_id": supplier_id},
            reason=f"proveedor no activo o no permitido {supplier_id}",
            blocking=True,
        )
    return PolicyCheckResult(
        policy_check_id="check_supplier_active",
        decision="pass",
        policy_id="supplier_active",
        facts={"supplier_id": supplier_id},
        reason="proveedor activo",
        blocking=False,
    )


def check_supplier_allowlist(
    supplier_id: str, tenant_id: str, allowlist: dict[str, set[str]] | None
) -> PolicyCheckResult:
    if allowlist is None:
        return PolicyCheckResult(
            policy_check_id="check_supplier_allowlist",
            decision="pass",
            policy_id="supplier_allowlist",
            facts={"supplier_id": supplier_id},
            reason="sin allowlist, se permite",
            blocking=False,
        )
    allowed = allowlist.get(tenant_id)
    if allowed is not None and supplier_id not in allowed:
        return PolicyCheckResult(
            policy_check_id="check_supplier_allowlist",
            decision="fail",
            policy_id="supplier_allowlist",
            facts={"supplier_id": supplier_id, "tenant_id": tenant_id},
            reason="proveedor no permitido para tenant",
            blocking=True,
        )
    return PolicyCheckResult(
        policy_check_id="check_supplier_allowlist",
        decision="pass",
        policy_id="supplier_allowlist",
        facts={"supplier_id": supplier_id},
        reason="proveedor permitido",
        blocking=False,
    )


def check_budget(
    total: float, tenant_id: str, location_id: str, budget_limits: dict[tuple[str, str], float]
) -> PolicyCheckResult:
    limit = (
        budget_limits.get((tenant_id, location_id))
        or budget_limits.get((tenant_id, "*"))
        or float("inf")
    )
    if total > limit:
        return PolicyCheckResult(
            policy_check_id="check_budget",
            decision="fail",
            policy_id="budget_limit",
            facts={
                "order_total": total,
                "delegated_limit": limit,
                "tenant_id": tenant_id,
                "location_id": location_id,
            },
            reason=f"total {total} excede límite delegado {limit}",
            blocking=True,
        )
    return PolicyCheckResult(
        policy_check_id="check_budget",
        decision="pass",
        policy_id="budget_limit",
        facts={"order_total": total, "delegated_limit": limit},
        reason="dentro de presupuesto",
        blocking=False,
    )


def check_quantity_limits_per_supplier(
    lines: list[ProposalLine], supplier_id: str, min_qty: float, max_qty: float
) -> PolicyCheckResult:
    for li in lines:
        if li.quantity < min_qty:
            return PolicyCheckResult(
                policy_check_id="check_quantity_limits",
                decision="fail",
                policy_id="quantity_min_max_per_supplier",
                facts={"sku": li.sku, "quantity": li.quantity, "min": min_qty},
                reason=f"cantidad {li.quantity} por debajo del mínimo {min_qty} para {supplier_id}",
                blocking=True,
            )
        if li.quantity > max_qty:
            return PolicyCheckResult(
                policy_check_id="check_quantity_limits",
                decision="fail",
                policy_id="quantity_min_max_per_supplier",
                facts={"sku": li.sku, "quantity": li.quantity, "max": max_qty},
                reason=f"cantidad {li.quantity} excede máximo {max_qty} para {supplier_id}",
                blocking=True,
            )
    return PolicyCheckResult(
        policy_check_id="check_quantity_limits",
        decision="pass",
        policy_id="quantity_min_max_per_supplier",
        facts={"supplier_id": supplier_id, "lines": len(lines)},
        reason="cantidades dentro de límites por proveedor",
        blocking=False,
    )


def check_duplicate(order_hash: str, existing_hashes: set[str]) -> PolicyCheckResult:
    if order_hash in existing_hashes:
        return PolicyCheckResult(
            policy_check_id="check_duplicate",
            decision="fail",
            policy_id="duplicate_order",
            facts={"order_hash": order_hash},
            reason="orden duplicada detectada",
            blocking=True,
        )
    return PolicyCheckResult(
        policy_check_id="check_duplicate",
        decision="pass",
        policy_id="duplicate_order",
        facts={"order_hash": order_hash},
        reason="no duplicada",
        blocking=False,
    )


def check_price_freshness(
    valid_until: datetime | None, now: datetime | None = None
) -> PolicyCheckResult:
    now = now or datetime.now(UTC)
    if valid_until and valid_until < now:
        return PolicyCheckResult(
            policy_check_id="check_price_freshness",
            decision="fail",
            policy_id="price_validity",
            facts={"valid_until": valid_until.isoformat(), "now": now.isoformat()},
            reason="evidencia de precio vencida",
            blocking=True,
        )
    return PolicyCheckResult(
        policy_check_id="check_price_freshness",
        decision="pass",
        policy_id="price_validity",
        facts={"valid_until": valid_until.isoformat() if valid_until else None},
        reason="precio vigente",
        blocking=False,
    )


# ---------------------------------------------------------------------------
# Orquestador de policy checks — agrega todos y determina si requiere approval
# ---------------------------------------------------------------------------
def run_policy_checks(
    *,
    proposal: Proposal,
    config: PolicyConfig,
    active_suppliers: set[str],
    existing_order_hashes: set[str] | None = None,
    supplier_min_max: dict[str, tuple[float, float]] | None = None,
) -> list[PolicyCheckResult]:
    checks: list[PolicyCheckResult] = []
    # 1. cantidad
    checks.append(check_quantity_non_negative(proposal.lines))
    # 2. unidad
    checks.append(check_unit_supported(proposal.lines, config.allowed_units))
    # 3. moneda
    checks.append(check_currency(proposal.lines, proposal.currency, config.allowed_currencies))
    # 4. supplier active
    checks.append(check_supplier_active(proposal.supplier_id, active_suppliers))
    # 5. allowlist
    # tenant_id lo extraemos de proposal? No está; asumimos tenant_demo si no hay; para Fase2 usamos config
    # Proposal no tiene tenant, usamos supplier_allowlist genérico
    if config.supplier_allowlist is not None:
        # necesitamos tenant; si no está, usamos "tenant_demo"
        checks.append(
            check_supplier_allowlist(proposal.supplier_id, "tenant_demo", config.supplier_allowlist)
        )
    # 6. presupuesto — necesitamos tenant/location; usar * fallback
    # proposal.execution_id no da location; usamos presupuesto wildcard
    checks.append(check_budget(proposal.total, "tenant_demo", "*", config.budget_limits))
    # 7. limites por proveedor
    if supplier_min_max and proposal.supplier_id in supplier_min_max:
        min_q, max_q = supplier_min_max[proposal.supplier_id]
        checks.append(
            check_quantity_limits_per_supplier(proposal.lines, proposal.supplier_id, min_q, max_q)
        )
    # 8. duplicado
    if existing_order_hashes is not None:
        # hash ya es scope_hash
        checks.append(check_duplicate(proposal.scope_hash, existing_order_hashes))
    # 9. vigencia precio — usamos estimated_delivery como proxy de valid_until?
    # En proposal lines estimated_delivery es futuro; si es pasado, fallaría
    # Tomamos max estimated_delivery
    if proposal.lines and proposal.lines[0].estimated_delivery:
        checks.append(check_price_freshness(proposal.lines[0].estimated_delivery))
    return checks


def has_blocking_failure(checks: list[PolicyCheckResult]) -> bool:
    return any(c.blocking and c.decision == "fail" for c in checks)


def requires_human_approval(
    checks: list[PolicyCheckResult], proposal_total: float, risk_level: str = "low"
) -> bool:
    # Regla MVP: siempre requiere approval si total>0 o riesgo != low o hay fail no blocking
    if has_blocking_failure(checks):
        # si hay blocking, no se puede auto-aprobar pero igual requiere revisión humana
        return True
    if proposal_total > 0:
        return True
    if risk_level != "low":
        return True
    return False
