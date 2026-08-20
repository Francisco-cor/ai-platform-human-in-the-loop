"""Simulador de proveedores — Fase 2.

Determinista, sin llamadas externas, sin LLM.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, Field

from procurement_platform.domain.inventory import SUPPORTED_CURRENCIES, round_currency
from procurement_platform.domain.models import ProposalLine


class Supplier(BaseModel):
    supplier_id: str
    name: str
    active: bool = True
    allowed_tenants: list[str] = Field(default_factory=list)
    allowed_locations: list[str] = Field(default_factory=list)  # vacío = todas
    currency: str = Field(default="USD")
    min_order_qty: float = Field(default=1, ge=0)
    max_order_qty: float = Field(default=10000, ge=0)
    lead_time_days: int = Field(default=7, ge=1)
    unit_price_overrides: dict[str, float] = Field(default_factory=dict)  # sku -> price


class SupplierQuote(BaseModel):
    supplier_id: str
    supplier_name: str
    sku: str
    unit: str = "piece"
    unit_price: float = Field(ge=0)
    currency: str = "USD"
    available_quantity: float = Field(ge=0)
    lead_time_days: int = Field(ge=1)
    valid_until: datetime
    # metadata para evidence
    is_active: bool = True


@dataclass(frozen=True)
class SupplierCatalog:
    suppliers: dict[str, Supplier]  # id -> Supplier
    base_prices: dict[str, float]  # sku -> base price USD

    def search(
        self,
        *,
        sku: str,
        quantity: float,
        unit: str = "piece",
        currency: str = "USD",
        tenant_id: str = "tenant_demo",
        location_id: str = "warehouse_north",
        horizon_days: int = 21,
        now: datetime | None = None,
    ) -> list[SupplierQuote]:
        now = now or datetime.now(UTC)
        results: list[SupplierQuote] = []
        base_price = self.base_prices.get(sku, 10.0)

        for sup in self.suppliers.values():
            if not sup.active:
                continue
            if sup.allowed_tenants and tenant_id not in sup.allowed_tenants:
                continue
            if sup.allowed_locations and location_id not in sup.allowed_locations:
                continue
            if currency != sup.currency and currency not in SUPPORTED_CURRENCIES:
                continue
            # cantidad dentro de límites (en unidad solicitada; asumimos piece para min/max)
            if quantity < sup.min_order_qty or quantity > sup.max_order_qty:
                continue
            # precio determinista: base * factor supplier + override
            price = sup.unit_price_overrides.get(sku, base_price)
            # pequeño ajuste determinista por supplier_id para ordenar
            # hash simple: sum ord ids % 5 -> 0-4% recargo
            recargo = (sum(ord(c) for c in sup.supplier_id) % 5) * 0.01
            price = round_currency(price * (1 + recargo))
            # moneda: si supplier es EUR y request es USD, simular conversión 1.1
            if sup.currency != currency:
                fx = {"USD": 1.0, "EUR": 1.1, "MXN": 0.055}
                # price en supplier currency -> convertir a requested currency
                # base es en USD, overrides asumen USD; simplificar: convertir
                price = round_currency(price * fx.get(sup.currency, 1.0) / fx.get(currency, 1.0))

            # disponible: si cantidad <= max, disponible = max
            available = sup.max_order_qty

            # lead time: si lead_time > horizon, igual se ofrece pero con riesgo
            lead_time = sup.lead_time_days

            valid_until = now + timedelta(days=30)

            results.append(
                SupplierQuote(
                    supplier_id=sup.supplier_id,
                    supplier_name=sup.name,
                    sku=sku,
                    unit=unit,
                    unit_price=price,
                    currency=currency,
                    available_quantity=available,
                    lead_time_days=lead_time,
                    valid_until=valid_until,
                    is_active=True,
                )
            )
        # ordenar por precio total (unit_price) y luego lead_time
        results.sort(key=lambda q: (q.unit_price, q.lead_time_days))
        return results

    def best_quote(
        self, *, sku: str, quantity: float, **kwargs
    ) -> SupplierQuote | None:
        quotes = self.search(sku=sku, quantity=quantity, **kwargs)
        return quotes[0] if quotes else None


def build_proposal_lines_from_shortages(
    *,
    shortages: list,
    catalog: SupplierCatalog,
    currency: str = "USD",
    tenant_id: str = "tenant_demo",
    location_id: str = "warehouse_north",
    horizon_days: int = 21,
    execution_id: str = "exec_demo",
) -> tuple[list[ProposalLine], list[str], list[str]]:
    """Construye líneas de propuesta a partir de faltantes y catálogo.

    Retorna (lines, missing_data, assumptions).
    Regla: quantity = max(shortage_qty, requested_qty) si shortage>0 else requested_qty.
    Si no hay supplier, se deja missing_data.
    """

    lines: list[ProposalLine] = []
    missing: list[str] = []
    assumptions: list[str] = []

    for s in shortages:  # type: ShortageResult
        # qty a pedir: si hay shortage, al menos shortage; si no, requested
        if s.shortage_qty > 0:
            qty = max(s.shortage_qty, s.requested_qty)
            if qty != s.shortage_qty:
                assumptions.append(f"{s.sku}: qty max(shortage {s.shortage_qty}, requested {s.requested_qty}) -> {qty}")
        else:
            qty = s.requested_qty
            if s.demand_total is not None and s.total_available >= s.demand_total:
                assumptions.append(f"{s.sku}: no shortage, using requested {qty}")

        quote = catalog.best_quote(
            sku=s.sku, quantity=qty, unit=s.unit, currency=currency, tenant_id=tenant_id, location_id=location_id, horizon_days=horizon_days
        )
        if not quote:
            missing.append(f"no_supplier_for:{s.sku}")
            continue
        if quote.valid_until < datetime.now(UTC):
            missing.append(f"quote_expired:{s.sku}@{quote.supplier_id}")
            continue
        # verificar moneda
        if quote.currency != currency:
            missing.append(f"currency_mismatch:{s.sku}:{quote.currency}!={currency}")

        lines.append(
            ProposalLine(
                sku=s.sku,
                quantity=round(qty, 2),
                unit=s.unit,
                unit_price=quote.unit_price,
                currency=currency,
                estimated_delivery=datetime.now(UTC) + timedelta(days=quote.lead_time_days),
            )
        )
        # collect missing from shortage
        missing.extend(s.missing_data)
        assumptions.extend(s.assumptions)

    return lines, missing, assumptions


def load_catalog_from_fixtures(suppliers_fixture: dict, base_prices: dict | None = None) -> SupplierCatalog:
    suppliers: dict[str, Supplier] = {}
    for s in suppliers_fixture.get("suppliers", []):
        sup = Supplier(
            supplier_id=s["supplier_id"],
            name=s.get("name", s["supplier_id"]),
            active=s.get("active", True),
            allowed_tenants=s.get("allowed_tenants", []),
            allowed_locations=s.get("allowed_locations", []),
            currency=s.get("currency", "USD"),
            min_order_qty=s.get("min_order", s.get("min_order_qty", 1)),
            max_order_qty=s.get("max_order", s.get("max_order_qty", 10000)),
            lead_time_days=s.get("lead_time_days", 7),
            unit_price_overrides=s.get("unit_price_overrides", {}),
        )
        suppliers[sup.supplier_id] = sup
    # base_prices puede venir de fixture quotes
    if base_prices is None:
        base_prices = {}
        for q in suppliers_fixture.get("quotes", []):
            sku = q["sku"]
            price = q["unit_price"]
            base_prices[sku] = price
        if not base_prices:
            base_prices = {"MAT-001": 10.0, "MAT-002": 25.0}
    return SupplierCatalog(suppliers=suppliers, base_prices=base_prices)
