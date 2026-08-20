
import pytest

from procurement_platform.domain.suppliers import (
    Supplier,
    SupplierCatalog,
    load_catalog_from_fixtures,
)


def test_supplier_search_basic():
    catalog = load_catalog_from_fixtures(
        {
            "suppliers": [
                {"supplier_id": "supplier_demo", "name": "Demo", "active": True, "allowed_tenants": ["tenant_demo"], "currency": "USD", "min_order": 1, "max_order": 1000, "lead_time_days": 7},
                {"supplier_id": "supplier_alt", "name": "Alt", "active": True, "allowed_tenants": ["tenant_demo"], "currency": "USD", "min_order": 1, "max_order": 1000, "lead_time_days": 5},
            ],
            "quotes": [{"sku": "MAT-001", "unit_price": 10.0}, {"sku": "MAT-002", "unit_price": 25.0}],
        }
    )
    quotes = catalog.search(sku="MAT-001", quantity=100, currency="USD", tenant_id="tenant_demo", location_id="warehouse_north")
    assert len(quotes) == 2
    # sorted by price then lead_time: both price similar (recargo diff), but determinista
    assert quotes[0].unit_price <= quotes[1].unit_price


def test_supplier_filter_inactive():
    catalog = SupplierCatalog(
        suppliers={
            "active_sup": Supplier(supplier_id="active_sup", name="Active", active=True, currency="USD"),
            "inactive_sup": Supplier(supplier_id="inactive_sup", name="Inactive", active=False, currency="USD"),
        },
        base_prices={"MAT-001": 10.0},
    )
    quotes = catalog.search(sku="MAT-001", quantity=10)
    assert len(quotes) == 1
    assert quotes[0].supplier_id == "active_sup"


def test_supplier_filter_tenant():
    catalog = load_catalog_from_fixtures(
        {
            "suppliers": [
                {"supplier_id": "sup_tenant_a", "name": "A", "active": True, "allowed_tenants": ["tenant_a"], "currency": "USD"},
                {"supplier_id": "sup_tenant_b", "name": "B", "active": True, "allowed_tenants": ["tenant_b"], "currency": "USD"},
            ],
            "quotes": [{"sku": "MAT-001", "unit_price": 10.0}],
        }
    )
    quotes_a = catalog.search(sku="MAT-001", quantity=10, tenant_id="tenant_a")
    assert any(q.supplier_id == "sup_tenant_a" for q in quotes_a)
    assert not any(q.supplier_id == "sup_tenant_b" for q in quotes_a)


def test_supplier_min_max_filter():
    catalog = SupplierCatalog(
        suppliers={
            "sup": Supplier(supplier_id="sup", name="Sup", active=True, currency="USD", min_order_qty=10, max_order_qty=100),
        },
        base_prices={"MAT-001": 10.0},
    )
    assert len(catalog.search(sku="MAT-001", quantity=5)) == 0  # below min
    assert len(catalog.search(sku="MAT-001", quantity=50)) == 1
    assert len(catalog.search(sku="MAT-001", quantity=200)) == 0  # above max


def test_supplier_currency_conversion():
    catalog = SupplierCatalog(
        suppliers={
            "sup_eur": Supplier(supplier_id="sup_eur", name="EUR Sup", active=True, currency="EUR"),
        },
        base_prices={"MAT-001": 10.0},
    )
    # request USD, supplier EUR => price converted 10*1.1=11 USD?
    quotes = catalog.search(sku="MAT-001", quantity=10, currency="USD")
    assert quotes[0].currency == "USD"
    assert quotes[0].unit_price == pytest.approx(11.0, rel=0.05)


def test_best_quote_deterministic():
    catalog = load_catalog_from_fixtures(
        {
            "suppliers": [
                {"supplier_id": "supplier_demo", "name": "Demo", "active": True, "currency": "USD"},
                {"supplier_id": "supplier_alt", "name": "Alt", "active": True, "currency": "USD"},
            ],
            "quotes": [{"sku": "MAT-001", "unit_price": 10.0}],
        }
    )
    q1 = catalog.best_quote(sku="MAT-001", quantity=10)
    q2 = catalog.best_quote(sku="MAT-001", quantity=10)
    assert q1.supplier_id == q2.supplier_id
    assert q1.unit_price == q2.unit_price


def test_build_proposal_lines_from_shortages():
    from procurement_platform.domain.inventory import load_context_from_fixtures
    from procurement_platform.domain.suppliers import build_proposal_lines_from_shortages

    ctx = load_context_from_fixtures(
        inventory_fixture={"location_id": "warehouse_north", "items": [{"sku": "MAT-001", "on_hand": 0, "reserved": 0, "in_transit": 0, "daily_demand_forecast": 5}]},
        open_orders_fixture=[],
    )
    from procurement_platform.domain.inventory import calculate_shortages

    shortages = calculate_shortages(items=[{"sku": "MAT-001", "quantity": 10, "unit": "piece"}], location_id="warehouse_north", horizon_days=10, ctx=ctx)
    # demand 50, shortage 50, requested 10 => qty max(50,10)=50
    catalog = load_catalog_from_fixtures({"suppliers": [{"supplier_id": "sup1", "name": "Sup1", "active": True, "currency": "USD"}], "quotes": [{"sku": "MAT-001", "unit_price": 5.0}]})
    lines, missing, assumptions = build_proposal_lines_from_shortages(shortages=shortages, catalog=catalog, currency="USD", tenant_id="tenant_demo", location_id="warehouse_north", horizon_days=10)
    assert len(lines) == 1
    assert lines[0].quantity == 50
    assert lines[0].unit_price == pytest.approx(5.0, rel=0.1)  # recargo 0-4%


def test_supplier_no_match_returns_empty():
    catalog = SupplierCatalog(suppliers={}, base_prices={})
    quotes = catalog.search(sku="UNKNOWN", quantity=10)
    assert quotes == []
