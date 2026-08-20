from datetime import UTC, datetime

from procurement_platform.persistence.database import get_sessionmaker
from procurement_platform.persistence.models import (
    DemandForecastRow,
    InventoryItem,
    PurchaseOrder,
    SupplierRow,
)


def test_inventory_crud():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    # clean
    db.query(InventoryItem).delete()
    db.query(DemandForecastRow).delete()
    db.query(SupplierRow).delete()
    db.query(PurchaseOrder).delete()
    db.commit()

    item = InventoryItem(
        id="inv_001",
        tenant_id="tenant_demo",
        sku="MAT-001",
        location_id="warehouse_north",
        on_hand=20,
        reserved=5,
        in_transit=0,
        unit="piece",
        lead_time_days=7,
        updated_at=datetime.now(UTC),
    )
    db.add(item)
    forecast = DemandForecastRow(
        id="dem_001",
        tenant_id="tenant_demo",
        sku="MAT-001",
        location_id="warehouse_north",
        daily_demand=8,
        unit="piece",
        updated_at=datetime.now(UTC),
    )
    db.add(forecast)
    supplier = SupplierRow(
        supplier_id="supplier_demo",
        tenant_id="tenant_demo",
        name="Demo Supplier",
        active=True,
        currency="USD",
        min_order_qty=1,
        max_order_qty=1000,
        lead_time_days=7,
        allowed_tenants=["tenant_demo"],
        allowed_locations=[],
        unit_price_overrides={"MAT-001": 10.0},
        updated_at=datetime.now(UTC),
    )
    db.add(supplier)
    po = PurchaseOrder(
        order_id="po_001",
        tenant_id="tenant_demo",
        sku="MAT-001",
        location_id="warehouse_north",
        quantity=15,
        unit="piece",
        supplier_id="supplier_demo",
        status="open",
        expected_arrival_days=5,
        created_at=datetime.now(UTC),
    )
    db.add(po)
    db.commit()

    # query
    assert db.get(InventoryItem, "inv_001") is not None
    assert db.get(DemandForecastRow, "dem_001") is not None
    assert db.get(SupplierRow, "supplier_demo") is not None
    assert db.get(PurchaseOrder, "po_001") is not None

    # verify unique index: duplicate should fail
    from sqlalchemy.exc import IntegrityError

    dup = InventoryItem(id="inv_dup", tenant_id="tenant_demo", sku="MAT-001", location_id="warehouse_north", on_hand=10, reserved=0, in_transit=0, unit="piece", updated_at=datetime.now(UTC))
    db.add(dup)
    try:
        db.commit()
        assert False, "should have raised IntegrityError for duplicate tenant/sku/location"
    except IntegrityError:
        db.rollback()

    db.close()


def test_purchase_order_duplicate_detection_via_db():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    db.query(PurchaseOrder).delete()
    db.commit()
    po = PurchaseOrder(order_id="po_dup_test", tenant_id="tenant_demo", sku="MAT-001", location_id="warehouse_north", quantity=100, unit="piece", supplier_id="sup1", status="open", expected_arrival_days=5, created_at=datetime.now(UTC))
    db.add(po)
    db.commit()
    # detect duplicate via domain logic
    from procurement_platform.domain.inventory import OpenPurchaseOrder, detect_duplicate_open_order

    open_orders = [
        OpenPurchaseOrder(order_id="po_dup_test", sku="MAT-001", location_id="warehouse_north", quantity=100, unit="piece", supplier_id="sup1", status="open", expected_arrival_days=5)
    ]
    dup = detect_duplicate_open_order(sku="MAT-001", location_id="warehouse_north", quantity=100, unit="piece", supplier_id="sup1", open_orders=open_orders)
    assert dup is not None
    db.close()
