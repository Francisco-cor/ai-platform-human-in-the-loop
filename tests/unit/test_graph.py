import pytest

from procurement_platform.persistence.models import WorkflowExecution
from procurement_platform.tools.gateway import ToolGateway, ToolGatewayError
from procurement_platform.workflows.graph import (
    calculate_shortage_node,
    draft_order_proposals_node,
    load_inventory_context_node,
    query_suppliers_node,
    retrieve_policies_node,
)


@pytest.mark.asyncio
async def test_graph_nodes_basic(db_session):
    db = db_session
    from procurement_platform.domain.models import NormalizedRequest, new_id
    from procurement_platform.workflows.orchestrator import WorkflowOrchestrator

    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(request_id=new_id("req"), tenant_id="tenant_demo", requester_id="user_01", items=[{"sku": "MAT-001", "quantity": 10, "unit": "piece"}], horizon_days=21, location_id="warehouse_north", currency="USD")
    exec_obj = orch.create_execution(db, normalized=norm)
    row = db.get(WorkflowExecution, exec_obj.execution_id)
    gateway = ToolGateway()

    res1 = await load_inventory_context_node(db, row, gateway, trace_id="trace_test")
    assert "inventory" in res1

    res2 = await retrieve_policies_node(db, row, gateway, trace_id="trace_test")
    assert "policies" in res2 or "error" in res2

    res3 = await calculate_shortage_node(db, row, gateway, trace_id="trace_test")
    assert "shortages" in res3 or "error" in res3

    res4 = await query_suppliers_node(db, row, gateway, trace_id="trace_test")
    assert "quotes" in res4 or "error" in res4


@pytest.mark.asyncio
async def test_graph_draft_proposal_with_llm(db_session):
    db = db_session
    from procurement_platform.domain.models import NormalizedRequest, new_id
    from procurement_platform.workflows.orchestrator import WorkflowOrchestrator

    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(request_id=new_id("req"), tenant_id="tenant_demo", requester_id="user_01", items=[{"sku": "MAT-001", "quantity": 10, "unit": "piece"}], horizon_days=21, location_id="warehouse_north", currency="USD")
    exec_obj = orch.create_execution(db, normalized=norm)
    row = db.get(WorkflowExecution, exec_obj.execution_id)

    res = await draft_order_proposals_node(db, row, trace_id="trace_test")
    assert "proposal" in res
    assert res["proposal"]["supplier_id"] == "supplier_demo"
    assert "model" in res


@pytest.mark.asyncio
async def test_graph_budget_exceeded(db_session):
    db = db_session
    from procurement_platform.domain.models import NormalizedRequest, new_id
    from procurement_platform.workflows.orchestrator import WorkflowOrchestrator

    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(request_id=new_id("req"), tenant_id="tenant_demo", requester_id="user_01", items=[{"sku": f"MAT-00{i}", "quantity": 10, "unit": "piece"} for i in range(10)], horizon_days=21, location_id="warehouse_north", currency="USD")
    exec_obj = orch.create_execution(db, normalized=norm)
    row = db.get(WorkflowExecution, exec_obj.execution_id)
    from procurement_platform.tools.gateway import ToolBudget

    gateway = ToolGateway(budget=ToolBudget(max_total_calls=20, max_supplier_queries=1))
    with pytest.raises(ToolGatewayError):
        await query_suppliers_node(db, row, gateway, trace_id="trace_test")
