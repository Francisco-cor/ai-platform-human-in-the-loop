from datetime import UTC, datetime, timedelta

from procurement_platform.domain.models import Proposal, ProposalLine
from procurement_platform.policies.engine import (
    PolicyConfig,
    check_budget,
    check_currency,
    check_duplicate,
    check_price_freshness,
    check_quantity_limits_per_supplier,
    check_quantity_non_negative,
    check_supplier_active,
    check_unit_supported,
    has_blocking_failure,
    run_policy_checks,
)


def make_proposal(
    total=100, currency="USD", supplier_id="supplier_demo", lines=None, qty=10
) -> Proposal:
    if lines is None:
        lines = [
            ProposalLine(
                sku="MAT-001",
                quantity=qty,
                unit="piece",
                unit_price=total / qty if qty else 10,
                currency=currency,
            )
        ]
    return Proposal(
        proposal_id="prop_test",
        request_id="req_test",
        execution_id="exec_test",
        supplier_id=supplier_id,
        supplier_name="Demo",
        lines=lines,
        subtotal=total,
        tax=0,
        total=total,
        currency=currency,
        scope_hash="sha256:abc",
    )


def test_quantity_non_negative():
    assert (
        check_quantity_non_negative(
            [ProposalLine(sku="A", quantity=1, unit="piece", unit_price=1)]
        ).decision
        == "pass"
    )
    res = check_quantity_non_negative(
        [ProposalLine(sku="A", quantity=0, unit="piece", unit_price=1)]
    )
    assert res.decision == "fail" and res.blocking


def test_unit_supported():
    res = check_unit_supported(
        [ProposalLine(sku="A", quantity=1, unit="piece", unit_price=1)], {"piece", "box"}
    )
    assert res.decision == "pass"
    res2 = check_unit_supported(
        [ProposalLine(sku="A", quantity=1, unit="invalid", unit_price=1)], {"piece"}
    )
    assert res2.decision == "fail"


def test_currency():
    assert (
        check_currency(
            [ProposalLine(sku="A", quantity=1, unit="piece", unit_price=1, currency="USD")],
            "USD",
            {"USD"},
        ).decision
        == "pass"
    )
    assert (
        check_currency(
            [ProposalLine(sku="A", quantity=1, unit="piece", unit_price=1, currency="USD")],
            "EUR",
            {"USD"},
        ).decision
        == "fail"
    )
    # line currency mismatch
    assert (
        check_currency(
            [ProposalLine(sku="A", quantity=1, unit="piece", unit_price=1, currency="EUR")],
            "USD",
            {"USD", "EUR"},
        ).decision
        == "fail"
    )


def test_supplier_active():
    assert check_supplier_active("sup1", {"sup1", "sup2"}).decision == "pass"
    assert check_supplier_active("sup3", {"sup1"}).decision == "fail"


def test_budget():
    cfg = PolicyConfig(budget_limits={("tenant_demo", "*"): 1000})
    # total 100 <1000 pass
    prop = make_proposal(total=100)
    res = check_budget(prop.total, "tenant_demo", "warehouse_north", cfg.budget_limits)
    assert res.decision == "pass"
    prop2 = make_proposal(total=2000)
    res2 = check_budget(prop2.total, "tenant_demo", "warehouse_north", cfg.budget_limits)
    assert res2.decision == "fail" and res2.blocking
    assert res2.facts["order_total"] == 2000


def test_quantity_limits_per_supplier():
    line = ProposalLine(sku="A", quantity=5, unit="piece", unit_price=1)
    res = check_quantity_limits_per_supplier([line], "sup1", min_qty=10, max_qty=100)
    assert res.decision == "fail"  # below min
    line2 = ProposalLine(sku="A", quantity=150, unit="piece", unit_price=1)
    res2 = check_quantity_limits_per_supplier([line2], "sup1", min_qty=10, max_qty=100)
    assert res2.decision == "fail"  # above max
    line3 = ProposalLine(sku="A", quantity=50, unit="piece", unit_price=1)
    res3 = check_quantity_limits_per_supplier([line3], "sup1", min_qty=10, max_qty=100)
    assert res3.decision == "pass"


def test_duplicate():
    assert check_duplicate("hash123", {"hash123"}).decision == "fail"
    assert check_duplicate("hash123", {"other"}).decision == "pass"


def test_price_freshness():
    now = datetime.now(UTC)
    future = now + timedelta(days=1)
    past = now - timedelta(days=1)
    assert check_price_freshness(future, now).decision == "pass"
    assert check_price_freshness(past, now).decision == "fail"


def test_run_policy_checks_integration():
    prop = make_proposal(total=5000, supplier_id="supplier_demo", qty=10)
    cfg = PolicyConfig(budget_limits={("tenant_demo", "*"): 1000})
    checks = run_policy_checks(
        proposal=prop, config=cfg, active_suppliers={"supplier_demo"}, existing_order_hashes=set()
    )
    # budget should fail
    assert any(c.policy_id == "budget_limit" and c.decision == "fail" for c in checks)
    assert has_blocking_failure(checks)

    # with higher limit, should pass
    cfg2 = PolicyConfig(budget_limits={("tenant_demo", "*"): 10000})
    checks2 = run_policy_checks(proposal=prop, config=cfg2, active_suppliers={"supplier_demo"})
    assert not has_blocking_failure(checks2)
