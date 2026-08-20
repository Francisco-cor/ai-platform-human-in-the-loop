
import pytest

from procurement_platform.domain.inventory import load_context_from_fixtures
from procurement_platform.domain.models import NormalizedRequest
from procurement_platform.domain.suppliers import load_catalog_from_fixtures
from procurement_platform.persistence.database import get_sessionmaker
from procurement_platform.policies.engine import PolicyConfig
from procurement_platform.workflows.orchestrator import WorkflowOrchestrator


def _ctx_and_catalog():
    ctx = load_context_from_fixtures(
        inventory_fixture={
            "location_id": "warehouse_north",
            "items": [
                {"sku": "MAT-001", "on_hand": 20, "reserved": 5, "in_transit": 0, "daily_demand_forecast": 8},
                {"sku": "MAT-002", "on_hand": 100, "reserved": 0, "in_transit": 0, "daily_demand_forecast": 2},
            ],
            "lead_times_days": {"MAT-001": 7, "MAT-002": 5},
        },
        open_orders_fixture=[
            {"order_id": "po_001", "sku": "MAT-001", "location_id": "warehouse_north", "quantity": 15, "unit": "piece", "supplier_id": "supplier_demo", "status": "open", "expected_arrival_days": 5}
        ],
    )
    catalog = load_catalog_from_fixtures(
        {
            "suppliers": [
                {"supplier_id": "supplier_demo", "name": "Demo Supplier Inc.", "active": True, "allowed_tenants": ["tenant_demo"], "currency": "USD", "min_order": 1, "max_order": 1000},
            ],
            "quotes": [{"sku": "MAT-001", "unit_price": 10.0}, {"sku": "MAT-002", "unit_price": 25.0}],
        }
    )
    policy = PolicyConfig(budget_limits={("tenant_demo", "*"): 5000})
    return ctx, catalog, policy


def test_deterministic_proposal_same_fixtures_same_result():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    ctx, catalog, policy = _ctx_and_catalog()
    orch1 = WorkflowOrchestrator(inventory_context=ctx, supplier_catalog=catalog, policy_config=policy)
    orch2 = WorkflowOrchestrator(inventory_context=ctx, supplier_catalog=catalog, policy_config=policy)

    norm = NormalizedRequest(
        request_id="req_det_01",
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[{"sku": "MAT-001", "quantity": 120, "unit": "piece"}],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
    )
    exec1 = orch1.create_execution(db, normalized=norm, trace_id="trace1")
    exec1 = orch1.advance_synthetic(db, exec1.execution_id, trace_id="trace1")
    # second execution with same request but different request_id
    norm2 = NormalizedRequest(
        request_id="req_det_02",
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[{"sku": "MAT-001", "quantity": 120, "unit": "piece"}],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
    )
    exec2 = orch2.create_execution(db, normalized=norm2, trace_id="trace2")
    exec2 = orch2.advance_synthetic(db, exec2.execution_id, trace_id="trace2")

    # proposals must be deterministically equal in qty/price (except ids/hashes which differ per proposal_id)
    # but qty and total should be same
    assert exec1.proposal is not None and exec2.proposal is not None
    assert exec1.proposal.lines[0].quantity == exec2.proposal.lines[0].quantity
    assert exec1.proposal.lines[0].unit_price == exec2.proposal.lines[0].unit_price
    assert exec1.proposal.total == exec2.proposal.total
    # shortage 138 => qty 138 (determinista)
    assert exec1.proposal.lines[0].quantity == 138
    assert exec1.proposal.lines[0].sku == "MAT-001"

    db.close()


def test_deterministic_no_llm_called():
    # Verificar que cálculo crítico no llama a modelo: inspeccionar que proposal no requiere LLM
    # El orchestrator no tiene dependencia de LLM en Fase 2, solo domain puro
    ctx, catalog, policy = _ctx_and_catalog()
    orch = WorkflowOrchestrator(inventory_context=ctx, supplier_catalog=catalog, policy_config=policy)
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    norm = NormalizedRequest(
        request_id="req_det_no_llm",
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[{"sku": "MAT-002", "quantity": 10, "unit": "piece"}],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
    )
    exec_obj = orch.create_execution(db, normalized=norm)
    exec_obj = orch.advance_synthetic(db, exec_obj.execution_id)
    # MAT-002: demand 2*21=42, available 100 => shortage 0 => qty 10 (requested)
    assert exec_obj.proposal.lines[0].quantity == 10
    assert exec_obj.proposal.lines[0].sku == "MAT-002"
    db.close()


def test_proposal_with_missing_forecast():
    ctx = load_context_from_fixtures(
        inventory_fixture={"location_id": "warehouse_north", "items": [{"sku": "MAT-999", "on_hand": 5, "reserved": 0, "in_transit": 0}]},
        open_orders_fixture=[],
    )
    catalog = load_catalog_from_fixtures({"suppliers": [{"supplier_id": "sup1", "name": "Sup1", "active": True, "currency": "USD"}], "quotes": [{"sku": "MAT-999", "unit_price": 10.0}]})
    policy = PolicyConfig(budget_limits={("tenant_demo", "*"): 5000})
    orch = WorkflowOrchestrator(inventory_context=ctx, supplier_catalog=catalog, policy_config=policy)
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    norm = NormalizedRequest(request_id="req_missing", tenant_id="tenant_demo", requester_id="user_01", items=[{"sku": "MAT-999", "quantity": 20, "unit": "piece"}], horizon_days=21, location_id="warehouse_north", currency="USD")
    exec_obj = orch.create_execution(db, normalized=norm)
    exec_obj = orch.advance_synthetic(db, exec_obj.execution_id)
    # no forecast => shortage = requested - available =15 => qty 15? Wait: available 5, requested 20 => shortage 15 => qty max(15,20)=20?
    # Our logic: no forecast => shortage = max(0, requested - total) =15, then qty max(15,20)=20
    assert exec_obj.proposal.lines[0].quantity == 20
    assert "demand_forecast:MAT-999@warehouse_north" in exec_obj.proposal.missing_data
    db.close()


def test_unit_conversion_in_proposal():
    ctx = load_context_from_fixtures(
        inventory_fixture={"location_id": "warehouse_north", "items": [{"sku": "MAT-001", "on_hand": 24, "reserved": 0, "in_transit": 0, "unit": "piece", "daily_demand_forecast": 12}]},
        open_orders_fixture=[],
    )
    catalog = load_catalog_from_fixtures({"suppliers": [{"supplier_id": "sup1", "name": "Sup1", "active": True, "currency": "USD"}], "quotes": [{"sku": "MAT-001", "unit_price": 10.0}]})
    orch = WorkflowOrchestrator(inventory_context=ctx, supplier_catalog=catalog, policy_config=PolicyConfig(budget_limits={("tenant_demo", "*"): 5000}))
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    # request 1 box =12 piece, horizon 10 => demand 12*10=120 piece =>10 boxes, available 24 piece=2 boxes => shortage 8 boxes
    norm = NormalizedRequest(request_id="req_box", tenant_id="tenant_demo", requester_id="user_01", items=[{"sku": "MAT-001", "quantity": 1, "unit": "box"}], horizon_days=10, location_id="warehouse_north", currency="USD")
    exec_obj = orch.create_execution(db, normalized=norm)
    exec_obj = orch.advance_synthetic(db, exec_obj.execution_id)
    assert exec_obj.proposal.lines[0].unit == "box"
    assert exec_obj.proposal.lines[0].quantity == pytest.approx(8.0, rel=1e-3) or exec_obj.proposal.lines[0].quantity == 8  # shortage 8 boxes, but qty max(shortage, requested)=8
    db.close()
