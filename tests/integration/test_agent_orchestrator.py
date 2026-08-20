from unittest.mock import patch

from procurement_platform.domain.models import NormalizedRequest, new_id
from procurement_platform.persistence.database import get_sessionmaker
from procurement_platform.workflows.orchestrator import WorkflowOrchestrator


def test_orchestrator_with_llm_happy_path():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id=new_id("req"),
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[{"sku": "MAT-001", "quantity": 50, "unit": "piece"}],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
    )
    exec_obj = orch.create_execution(db, normalized=norm)
    exec_obj = orch.advance_synthetic(db, exec_obj.execution_id)
    # Fase 4: debe producir propuesta válida aunque use LLM
    assert exec_obj.proposal is not None
    assert exec_obj.proposal.total == round(
        exec_obj.proposal.lines[0].quantity * exec_obj.proposal.lines[0].unit_price, 2
    )
    # evidence debe mencionar LLM o determinista
    assert exec_obj.proposal.evidence is not None
    assert exec_obj.status.value == "AWAITING_APPROVAL"
    db.close()


def test_orchestrator_llm_invalid_output_fallback():
    # Simula LLM que devuelve JSON inválido, debe hacer fallback a determinista sin efecto externo
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    # Crear orchestrator normal, pero parchear _call_llm_for_proposal para retornar invalid
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id=new_id("req"),
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[{"sku": "MAT-001", "quantity": 10, "unit": "piece"}],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
    )
    exec_obj = orch.create_execution(db, normalized=norm)
    # Parchear para que llm retorne invalid
    with patch.object(orch, "_call_llm_for_proposal", return_value=None):
        exec_obj = orch.advance_synthetic(db, exec_obj.execution_id)
        # Debe seguir produciendo propuesta determinista
        assert exec_obj.proposal is not None
        assert exec_obj.proposal.lines[0].quantity > 0
        assert exec_obj.status.value == "AWAITING_APPROVAL"
    db.close()


def test_orchestrator_tool_budget_exceeded_blocks():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    orch = WorkflowOrchestrator()
    # Crear request con muchos items para exceder budget de supplier queries (default 5)
    items = [{"sku": f"MAT-00{i}", "quantity": 10, "unit": "piece"} for i in range(10)]
    norm = NormalizedRequest(
        request_id=new_id("req"),
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=items,
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
    )
    exec_obj = orch.create_execution(db, normalized=norm)
    # advance_synthetic debería detectar budget_exceeded y pasar a BLOCKED
    exec_obj = orch.advance_synthetic(db, exec_obj.execution_id)
    # Como tenemos 10 items y max_supplier_queries 5, debería bloquear
    assert exec_obj.status.value == "BLOCKED"
    db.close()


def test_orchestrator_llm_fallback_chain():
    # Verifica que si Gemini no está disponible, usa fake sin error
    from procurement_platform.agents.factory import LLMFactory
    from procurement_platform.agents.adapter import LLMRequest

    req = LLMRequest(system_prompt="system", user_prompt="test fallback", response_schema=None)
    # Con provider auto y sin keys, debe usar fake
    import asyncio

    resp = asyncio.run(LLMFactory.generate_with_fallback(req))
    assert resp.provider == "fake"
    # was_fallback puede ser True si vino de fallback chain, pero debe ser fake de todas formas
    assert resp.provider == "fake"


def test_api_with_agent_proposal(client):
    # Test via API que la propuesta incluye evidence con LLM y que no se confía en total del LLM
    payload = {
        "tenant_id": "tenant_demo",
        "requester_id": "user_01",
        "items": [{"sku": "MAT-001", "quantity": 15, "unit": "piece"}],
        "horizon_days": 21,
        "location_id": "warehouse_north",
        "currency": "USD",
    }
    resp = client.post("/v1/procurement/executions", json=payload)
    assert resp.status_code == 202, resp.text
    exec_id = resp.json()["execution_id"]
    full = client.get(f"/v1/procurement/executions/{exec_id}").json()
    proposal = full["proposal"]
    assert proposal is not None
    # total debe ser recalculado, no el del LLM
    line = proposal["lines"][0]
    assert proposal["total"] == round(line["quantity"] * line["unit_price"], 2)
    # evidence debe existir
    assert "evidence" in proposal and len(proposal["evidence"]) > 0
