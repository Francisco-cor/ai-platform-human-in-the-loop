import pytest

from procurement_platform.domain.inventory import (
    InventoryContext,
    OpenPurchaseOrder,
    calculate_shortage_for_item,
    calculate_shortages,
    convert_quantity,
    detect_duplicate_open_order,
    load_context_from_fixtures,
)


def test_calculate_shortage_happy_path():
    ctx = load_context_from_fixtures(
        inventory_fixture={
            "location_id": "warehouse_north",
            "items": [
                {"sku": "MAT-001", "on_hand": 20, "reserved": 5, "in_transit": 0, "daily_demand_forecast": 8},
            ],
            "lead_times_days": {"MAT-001": 7},
        },
        open_orders_fixture=[
            {"order_id": "po_001", "sku": "MAT-001", "location_id": "warehouse_north", "quantity": 15, "unit": "piece", "supplier_id": "supplier_demo", "status": "open", "expected_arrival_days": 5}
        ],
    )
    res = calculate_shortage_for_item(sku="MAT-001", requested_qty=120, requested_unit="piece", location_id="warehouse_north", horizon_days=21, ctx=ctx)
    assert res.demand_total == 168  # 8*21
    assert res.available == 15  # 20-5
    assert res.in_transit_considered == 15
    assert res.total_available == 30
    assert res.shortage_qty == 138  # 168-30
    assert res.coverage_days == pytest.approx(30 / 8, rel=1e-3)
    assert res.missing_data == []


def test_calculate_shortage_missing_snapshot():
    ctx = InventoryContext(snapshots={}, forecasts={}, open_orders=[])
    res = calculate_shortage_for_item(sku="MAT-999", requested_qty=10, requested_unit="piece", location_id="warehouse_north", horizon_days=21, ctx=ctx)
    assert "inventory_snapshot:MAT-999@warehouse_north" in res.missing_data
    assert res.available == 0
    assert res.shortage_qty == 10  # no forecast => based on requested
    assert "snapshot_missing_treated_as_zero" in res.assumptions


def test_calculate_shortage_missing_forecast():
    ctx = load_context_from_fixtures(
        inventory_fixture={"location_id": "warehouse_north", "items": [{"sku": "MAT-001", "on_hand": 100, "reserved": 0, "in_transit": 0}]},
        open_orders_fixture=[],
    )
    # no forecast
    res = calculate_shortage_for_item(sku="MAT-001", requested_qty=20, requested_unit="piece", location_id="warehouse_north", horizon_days=21, ctx=ctx)
    assert "demand_forecast:MAT-001@warehouse_north" in res.missing_data
    assert res.demand_total is None
    assert res.shortage_qty == 0  # 20 -100 => 0? Wait: available 100, requested 20 => shortage 0 (since no forecast, shortage = max(0, requested - total) =0)
    # but with available 100, requested 20 => 0
    assert res.shortage_qty == 0


def test_unit_conversion_piece_box():
    assert convert_quantity(1, "box", "piece") == 12
    assert convert_quantity(12, "piece", "box") == pytest.approx(1.0)
    # shortage with different units
    ctx = load_context_from_fixtures(
        inventory_fixture={"location_id": "warehouse_north", "items": [{"sku": "MAT-001", "on_hand": 24, "reserved": 0, "in_transit": 0, "unit": "piece", "daily_demand_forecast": 12}]},
        open_orders_fixture=[],
    )
    # request 1 box =12 piece, horizon 10 => demand 12*10=120 piece => 10 boxes
    res = calculate_shortage_for_item(sku="MAT-001", requested_qty=1, requested_unit="box", location_id="warehouse_north", horizon_days=10, ctx=ctx)
    # available 24 piece =>2 boxes, demand 10 boxes => shortage 8 boxes
    assert res.shortage_qty == pytest.approx(8.0, rel=1e-3)
    assert res.unit == "box"


def test_in_transit_arrival_after_horizon_ignored():
    ctx = load_context_from_fixtures(
        inventory_fixture={"location_id": "warehouse_north", "items": [{"sku": "MAT-001", "on_hand": 10, "reserved": 0, "in_transit": 0, "daily_demand_forecast": 5}]},
        open_orders_fixture=[
            {"order_id": "po_late", "sku": "MAT-001", "location_id": "warehouse_north", "quantity": 100, "unit": "piece", "supplier_id": "s1", "status": "open", "expected_arrival_days": 30}
        ],
    )
    res = calculate_shortage_for_item(sku="MAT-001", requested_qty=10, requested_unit="piece", location_id="warehouse_north", horizon_days=21, ctx=ctx)
    # in_transit late => ignored => total 10, demand 105 => shortage 95
    assert res.in_transit_considered == 0
    assert res.shortage_qty == 95
    assert any("arrives_after_horizon" in a for a in res.assumptions)


def test_reserved_greater_than_on_hand():
    ctx = load_context_from_fixtures(
        inventory_fixture={"location_id": "warehouse_north", "items": [{"sku": "MAT-001", "on_hand": 5, "reserved": 10, "in_transit": 0, "daily_demand_forecast": 2}]},
        open_orders_fixture=[],
    )
    res = calculate_shortage_for_item(sku="MAT-001", requested_qty=5, requested_unit="piece", location_id="warehouse_north", horizon_days=10, ctx=ctx)
    assert res.available == 0  # max(0, 5-10)
    assert res.demand_total == 20
    assert res.shortage_qty == 20


def test_detect_duplicate_open_order():
    open_orders = [
        OpenPurchaseOrder(order_id="po1", sku="MAT-001", location_id="warehouse_north", quantity=100, unit="piece", supplier_id="sup1", status="open", expected_arrival_days=5),
        OpenPurchaseOrder(order_id="po2", sku="MAT-001", location_id="warehouse_north", quantity=50, unit="piece", supplier_id="sup2", status="open", expected_arrival_days=5),
    ]
    dup = detect_duplicate_open_order(sku="MAT-001", location_id="warehouse_north", quantity=100, unit="piece", supplier_id="sup1", open_orders=open_orders)
    assert dup is not None and dup.order_id == "po1"
    # tolerance 5%: 105 vs 100 => within 5% => duplicate
    dup2 = detect_duplicate_open_order(sku="MAT-001", location_id="warehouse_north", quantity=105, unit="piece", supplier_id="sup1", open_orders=open_orders)
    assert dup2 is not None
    # different supplier not duplicate
    dup3 = detect_duplicate_open_order(sku="MAT-001", location_id="warehouse_north", quantity=100, unit="piece", supplier_id="sup3", open_orders=open_orders)
    assert dup3 is None
    # quantity far => not duplicate
    dup4 = detect_duplicate_open_order(sku="MAT-001", location_id="warehouse_north", quantity=200, unit="piece", supplier_id="sup1", open_orders=open_orders)
    assert dup4 is None


def test_calculate_shortages_multiple_items_and_determinism():
    ctx = load_context_from_fixtures(
        inventory_fixture={
            "location_id": "warehouse_north",
            "items": [
                {"sku": "MAT-001", "on_hand": 20, "reserved": 5, "in_transit": 0, "daily_demand_forecast": 8},
                {"sku": "MAT-002", "on_hand": 100, "reserved": 0, "in_transit": 0, "daily_demand_forecast": 2},
            ]
        },
        open_orders_fixture=[
            {"order_id": "po1", "sku": "MAT-001", "location_id": "warehouse_north", "quantity": 15, "unit": "piece", "supplier_id": "s1", "status": "open", "expected_arrival_days": 5}
        ],
    )
    items = [{"sku": "MAT-001", "quantity": 120, "unit": "piece"}, {"sku": "MAT-002", "quantity": 10, "unit": "piece"}]
    first = calculate_shortages(items=items, location_id="warehouse_north", horizon_days=21, ctx=ctx)
    second = calculate_shortages(items=items, location_id="warehouse_north", horizon_days=21, ctx=ctx)
    assert first[0].shortage_qty == second[0].shortage_qty
    assert first[1].shortage_qty == second[1].shortage_qty
    # MAT-001 shortage 138, MAT-002 demand 42, available 100 => shortage 0
    assert first[0].shortage_qty == 138
    assert first[1].shortage_qty == 0


def test_unsupported_unit_raises():
    ctx = InventoryContext(snapshots={}, forecasts={}, open_orders=[])
    with pytest.raises(ValueError, match="unidad no soportada"):
        calculate_shortages(items=[{"sku": "MAT-001", "quantity": 10, "unit": "invalid_unit"}], location_id="warehouse_north", horizon_days=21, ctx=ctx)


def test_currency_rounding():
    from procurement_platform.domain.inventory import round_currency

    assert round_currency(10.005) == 10.01
    assert round_currency(10.004) == 10.0
