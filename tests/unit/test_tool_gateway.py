import pytest

from procurement_platform.domain.models import ExecutionState
from procurement_platform.tools.gateway import ToolBudget, ToolGateway, ToolGatewayError


def test_gateway_allowlist():
    gateway = ToolGateway()
    # get_inventory permitido en RECEIVED
    assert gateway.call(tool_name="get_inventory", payload={"sku": "MAT-001", "location_id": "warehouse_north"}, execution_id="exec_1", state=ExecutionState.RECEIVED) is not None
    # submit_purchase_order no permitido en RECEIVED
    with pytest.raises(ToolGatewayError, match="not_allowed_for_state"):
        gateway.call(tool_name="submit_purchase_order", payload={"proposal_id": "prop_1"}, execution_id="exec_1", state=ExecutionState.RECEIVED)


def test_gateway_validation():
    gateway = ToolGateway()
    # falta campo requerido
    with pytest.raises(ToolGatewayError, match="validation_error"):
        gateway.call(tool_name="get_inventory", payload={"sku": "MAT-001"}, execution_id="exec_1", state=ExecutionState.RECEIVED)
    # tipo incorrecto
    with pytest.raises(ToolGatewayError, match="validation_error"):
        gateway.call(tool_name="get_inventory", payload={"sku": "MAT-001", "location_id": 123}, execution_id="exec_1", state=ExecutionState.RECEIVED)


def test_gateway_approval_required():
    gateway = ToolGateway()
    # submit requiere aprobación
    with pytest.raises(ToolGatewayError, match="approval_required"):
        gateway.call(tool_name="submit_purchase_order", payload={"proposal_id": "prop_1"}, execution_id="exec_1", state=ExecutionState.POLICY_CHECKED, has_approval=False)
    # con aprobación pasa
    assert gateway.call(tool_name="submit_purchase_order", payload={"proposal_id": "prop_1"}, execution_id="exec_1", state=ExecutionState.POLICY_CHECKED, has_approval=True) is not None


def test_gateway_budget():
    budget = ToolBudget(max_total_calls=2, max_supplier_queries=1, max_proposals=1)
    gateway = ToolGateway(budget=budget)
    gateway.call(tool_name="get_inventory", payload={"sku": "A", "location_id": "loc"}, execution_id="exec_1", state=ExecutionState.RECEIVED)
    gateway.call(tool_name="get_inventory", payload={"sku": "B", "location_id": "loc"}, execution_id="exec_1", state=ExecutionState.RECEIVED)
    with pytest.raises(ToolGatewayError, match="budget_exceeded"):
        gateway.call(tool_name="get_inventory", payload={"sku": "C", "location_id": "loc"}, execution_id="exec_1", state=ExecutionState.RECEIVED)

    # supplier queries budget
    budget2 = ToolBudget(max_total_calls=10, max_supplier_queries=1)
    gateway2 = ToolGateway(budget=budget2)
    gateway2.call(tool_name="search_suppliers", payload={"sku": "MAT-001", "quantity": 10}, execution_id="exec_1", state=ExecutionState.CONTEXT_LOADED)
    with pytest.raises(ToolGatewayError, match="budget_exceeded"):
        gateway2.call(tool_name="search_suppliers", payload={"sku": "MAT-002", "quantity": 10}, execution_id="exec_1", state=ExecutionState.CONTEXT_LOADED)


def test_gateway_idempotency():
    gateway = ToolGateway()
    payload = {"sku": "MAT-001", "location_id": "warehouse_north"}
    res1 = gateway.call(tool_name="get_inventory", payload=payload, execution_id="exec_1", state=ExecutionState.RECEIVED)
    res2 = gateway.call(tool_name="get_inventory", payload=payload, execution_id="exec_1", state=ExecutionState.RECEIVED)
    assert res1 == res2
    # diferente payload no es idempotente
    res3 = gateway.call(tool_name="get_inventory", payload={"sku": "MAT-002", "location_id": "warehouse_north"}, execution_id="exec_1", state=ExecutionState.RECEIVED)
    assert res3 is not None
    assert len(gateway.call_log) == 2  # solo 2 llamadas únicas logueadas (la segunda fue cache)


def test_gateway_unknown_tool():
    gateway = ToolGateway()
    with pytest.raises(ToolGatewayError, match="unknown_tool"):
        gateway.call(tool_name="unknown_tool", payload={}, execution_id="exec_1", state=ExecutionState.RECEIVED)
