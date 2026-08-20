"""Dominio determinista de inventario — Fase 2.

Cálculos puros, sin LLM, sin DB, sin efectos externos.
Todos los fixtures producen el mismo resultado.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

# ---------------------------------------------------------------------------
# Unidades y moneda — §8 y §9
# ---------------------------------------------------------------------------
UNIT_CONVERSIONS: dict[tuple[str, str], float] = {
    # (from, to) -> factor (multiply qty in `from` to get qty in `to`)
    ("piece", "piece"): 1.0,
    ("box", "piece"): 12.0,
    ("piece", "box"): 1 / 12.0,
    ("kg", "kg"): 1.0,
    ("g", "kg"): 0.001,
    ("kg", "g"): 1000.0,
    ("liter", "liter"): 1.0,
    ("ml", "liter"): 0.001,
    ("liter", "ml"): 1000.0,
}

SUPPORTED_CURRENCIES = {"USD", "EUR", "MXN"}
SUPPORTED_UNITS = {"piece", "box", "kg", "g", "liter", "ml"}


def convert_quantity(qty: float, from_unit: str, to_unit: str) -> float:
    if from_unit == to_unit:
        return qty
    key = (from_unit, to_unit)
    if key not in UNIT_CONVERSIONS:
        raise ValueError(f"conversion no soportada: {from_unit} -> {to_unit}")
    return qty * UNIT_CONVERSIONS[key]


def round_currency(amount: float) -> float:
    return round(amount, 2)


# ---------------------------------------------------------------------------
# Modelos de inventario
# ---------------------------------------------------------------------------
class InventorySnapshot(BaseModel):
    sku: str
    location_id: str
    on_hand: float = Field(ge=0)
    reserved: float = Field(ge=0, default=0)
    in_transit: float = Field(ge=0, default=0)
    unit: str = Field(default="piece")
    # lead time por sku (días) — puede venir de demand_forecast o supplier
    lead_time_days: int | None = Field(default=None, ge=1)

    @property
    def available(self) -> float:
        # disponible neto sin contar tránsito
        return max(0.0, self.on_hand - self.reserved)


class DemandForecast(BaseModel):
    sku: str
    location_id: str
    daily_demand: float = Field(ge=0)
    unit: str = Field(default="piece")


class OpenPurchaseOrder(BaseModel):
    order_id: str
    sku: str
    location_id: str
    quantity: float = Field(gt=0)
    unit: str = Field(default="piece")
    supplier_id: str
    status: Literal["open", "closed", "cancelled"] = "open"
    # días hasta llegada esperada (0 = ya llegó, None = desconocido)
    expected_arrival_days: int | None = Field(default=None, ge=0)


# ---------------------------------------------------------------------------
# Resultado de faltantes
# ---------------------------------------------------------------------------
class ShortageResult(BaseModel):
    sku: str
    location_id: str
    unit: str
    requested_qty: float
    requested_qty_in_snapshot_unit: float
    daily_demand: float | None
    daily_demand_unit: str | None
    horizon_days: int
    demand_total: float | None  # daily_demand * horizon
    available: float
    in_transit_considered: float
    total_available: float
    shortage_qty: float  # max(0, demand_total + requested? ver nota)
    coverage_days: float | None  # available / daily_demand si daily_demand>0
    missing_data: list[str] = Field(default_factory=list)
    assumptions: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class InventoryContext:
    snapshots: dict[tuple[str, str], InventorySnapshot]  # (sku, location) -> snapshot
    forecasts: dict[tuple[str, str], DemandForecast]
    open_orders: list[OpenPurchaseOrder]


def calculate_shortage_for_item(
    *,
    sku: str,
    requested_qty: float,
    requested_unit: str,
    location_id: str,
    horizon_days: int,
    ctx: InventoryContext,
) -> ShortageResult:
    missing: list[str] = []
    assumptions: list[str] = []

    snap_key = (sku, location_id)
    snapshot = ctx.snapshots.get(snap_key)
    forecast = ctx.forecasts.get(snap_key)

    if snapshot is None:
        missing.append(f"inventory_snapshot:{sku}@{location_id}")
        # snapshot por defecto: 0
        snapshot = InventorySnapshot(sku=sku, location_id=location_id, on_hand=0, reserved=0, in_transit=0, unit=requested_unit)
        assumptions.append("snapshot_missing_treated_as_zero")

    if forecast is None:
        missing.append(f"demand_forecast:{sku}@{location_id}")
        daily_demand = None
        daily_demand_unit = None
        demand_total = None
    else:
        # Convertir daily_demand a unidad solicitada si es distinta
        try:
            if forecast.unit != requested_unit:
                daily_demand = convert_quantity(forecast.daily_demand, forecast.unit, requested_unit)
                assumptions.append(f"converted_demand_{forecast.unit}_to_{requested_unit}")
                daily_demand_unit = requested_unit
            else:
                daily_demand = forecast.daily_demand
                daily_demand_unit = forecast.unit
        except ValueError as e:
            missing.append(f"unit_conversion_failed:{e}")
            daily_demand = forecast.daily_demand
            daily_demand_unit = forecast.unit
            assumptions.append("unit_conversion_failed_used_raw_demand")
        demand_total = daily_demand * horizon_days if daily_demand is not None else None

    # Convertir requested_qty a unidad de snapshot para cálculos de disponible
    # Pero para shortage usamos unidad solicitada como canónica del RequestItem
    # Necesitamos snapshot.available en requested_unit
    try:
        available_in_requested_unit = convert_quantity(snapshot.available, snapshot.unit, requested_unit)
        if snapshot.unit != requested_unit:
            assumptions.append(f"converted_available_{snapshot.unit}_to_{requested_unit}")
    except ValueError:
        missing.append(f"unit_conversion_failed_available:{snapshot.unit}->{requested_unit}")
        available_in_requested_unit = snapshot.available
        assumptions.append("available_conversion_failed_used_raw")

    # snapshot in_transit: se considera como cantidad que llegará (asunción: llega dentro del horizonte)
    try:
        snapshot_in_transit = convert_quantity(snapshot.in_transit, snapshot.unit, requested_unit)
        if snapshot.in_transit > 0:
            assumptions.append(f"snapshot_in_transit_{snapshot.in_transit}_{snapshot.unit}_included")
    except ValueError:
        snapshot_in_transit = 0
        missing.append(f"unit_conversion_failed_snapshot_in_transit:{snapshot.unit}->{requested_unit}")

    # In-transit de órdenes abiertas considerado solo si arrival <= horizon_days
    in_transit_considered = snapshot_in_transit
    for oo in ctx.open_orders:
        if oo.sku != sku or oo.location_id != location_id:
            continue
        if oo.status != "open":
            continue
        if oo.expected_arrival_days is None:
            assumptions.append(f"open_order_{oo.order_id}_arrival_unknown_ignored")
            continue
        if oo.expected_arrival_days <= horizon_days:
            try:
                qty_in_req_unit = convert_quantity(oo.quantity, oo.unit, requested_unit)
                in_transit_considered += qty_in_req_unit
            except ValueError:
                missing.append(f"unit_conversion_failed_open_order:{oo.order_id}")
                continue
        else:
            assumptions.append(f"open_order_{oo.order_id}_arrives_after_horizon_ignored")

    total_available = available_in_requested_unit + in_transit_considered

    # shortage = max(0, demand_total - total_available) si hay forecast,
    # si no hay forecast, shortage = max(0, requested_qty - total_available) ?
    # Regla Fase 2: si hay forecast, shortage considera demanda + requested como demanda adicional?
    # Decisión: shortage = max(0, (demand_total or 0) + requested_qty - total_available) si demand_total existe,
    # pero eso duplica si requested ya está incluido en demanda. Más preciso: shortage = max(0, demand_total - total_available) y proposal_qty = max(shortage, requested_qty)
    # Para ShortageResult, reportamos shortage basado solo en demanda, y dejamos que proposal logic decida.
    # Si no hay forecast, shortage = max(0, requested_qty - total_available)
    if demand_total is not None:
        shortage = max(0.0, demand_total - total_available)
        # también asegurar que al menos se cubra lo solicitado si shortage < requested pero total_available insuficiente para requested
        if requested_qty > total_available and requested_qty > shortage:
            # No es shortage por demanda sino por request directo; lo reportamos como faltante adicional
            # pero mantenemos shortage como demanda-based; missing_data indicará
            pass
    else:
        shortage = max(0.0, requested_qty - total_available)
        assumptions.append("no_forecast_shortage_based_on_requested_qty")

    # coverage
    if daily_demand is not None and daily_demand > 0:
        coverage_days = total_available / daily_demand
    else:
        coverage_days = None

    # Convertir requested a snapshot unit para reporting
    try:
        requested_in_snapshot_unit = convert_quantity(requested_qty, requested_unit, snapshot.unit)
    except ValueError:
        requested_in_snapshot_unit = requested_qty

    return ShortageResult(
        sku=sku,
        location_id=location_id,
        unit=requested_unit,
        requested_qty=requested_qty,
        requested_qty_in_snapshot_unit=requested_in_snapshot_unit,
        daily_demand=daily_demand,
        daily_demand_unit=daily_demand_unit,
        horizon_days=horizon_days,
        demand_total=demand_total,
        available=available_in_requested_unit,
        in_transit_considered=in_transit_considered,
        total_available=total_available,
        shortage_qty=round(shortage, 2),
        coverage_days=round(coverage_days, 2) if coverage_days is not None else None,
        missing_data=missing,
        assumptions=assumptions,
    )


def calculate_shortages(
    *,
    items: list[dict],  # list of {sku, quantity, unit}
    location_id: str,
    horizon_days: int,
    ctx: InventoryContext,
) -> list[ShortageResult]:
    results: list[ShortageResult] = []
    for it in items:
        sku = it["sku"]
        qty = float(it["quantity"])
        unit = it.get("unit", "piece")
        # validaciones deterministas previas
        if qty <= 0:
            raise ValueError(f"cantidad no positiva para {sku}: {qty}")
        if unit not in SUPPORTED_UNITS:
            raise ValueError(f"unidad no soportada: {unit}")
        res = calculate_shortage_for_item(
            sku=sku,
            requested_qty=qty,
            requested_unit=unit,
            location_id=location_id,
            horizon_days=horizon_days,
            ctx=ctx,
        )
        results.append(res)
    return results


def detect_duplicate_open_order(
    *,
    sku: str,
    location_id: str,
    quantity: float,
    unit: str,
    supplier_id: str,
    open_orders: list[OpenPurchaseOrder],
    tolerance_pct: float = 0.05,
) -> OpenPurchaseOrder | None:
    """Detecta orden abierta duplicada para mismo sku/location/proveedor con cantidad similar."""
    for oo in open_orders:
        if oo.status != "open":
            continue
        if oo.sku != sku or oo.location_id != location_id or oo.supplier_id != supplier_id:
            continue
        try:
            qty_in_req_unit = convert_quantity(oo.quantity, oo.unit, unit)
        except ValueError:
            continue
        # tolerancia 5% por defecto
        if abs(qty_in_req_unit - quantity) / max(quantity, 1) <= tolerance_pct:
            return oo
    return None


def load_context_from_fixtures(
    *,
    inventory_fixture: dict,
    demand_fixture: dict | None = None,
    open_orders_fixture: list[dict] | None = None,
) -> InventoryContext:
    """Helper para tests — carga contexto desde JSON fixtures similares a evals/fixtures."""
    snapshots: dict[tuple[str, str], InventorySnapshot] = {}
    forecasts: dict[tuple[str, str], DemandForecast] = {}
    location_id = inventory_fixture.get("location_id", "warehouse_north")
    for item in inventory_fixture.get("items", []):
        sku = item["sku"]
        snap = InventorySnapshot(
            sku=sku,
            location_id=location_id,
            on_hand=float(item.get("on_hand", 0)),
            reserved=float(item.get("reserved", 0)),
            in_transit=float(item.get("in_transit", 0)),
            unit=item.get("unit", "piece"),
            lead_time_days=item.get("lead_time_days") or inventory_fixture.get("lead_times_days", {}).get(sku),
        )
        snapshots[(sku, location_id)] = snap
        # demanda puede venir en mismo fixture (Fase 1) o separado
        if "daily_demand_forecast" in item or "daily_demand" in item:
            dd = item.get("daily_demand_forecast", item.get("daily_demand", 0))
            forecasts[(sku, location_id)] = DemandForecast(
                sku=sku, location_id=location_id, daily_demand=float(dd), unit=item.get("unit", "piece")
            )
    # demanda separada
    if demand_fixture:
        for item in demand_fixture.get("items", []):
            sku = item["sku"]
            loc = item.get("location_id", location_id)
            forecasts[(sku, loc)] = DemandForecast(sku=sku, location_id=loc, daily_demand=float(item["daily_demand"]), unit=item.get("unit", "piece"))

    open_orders: list[OpenPurchaseOrder] = []
    for oo in open_orders_fixture or []:
        open_orders.append(OpenPurchaseOrder(**oo))

    return InventoryContext(snapshots=snapshots, forecasts=forecasts, open_orders=open_orders)
