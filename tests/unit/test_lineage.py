"""Fase 9 — lineage tests."""

from procurement_platform.domain.models import NormalizedRequest, RequestItem
from procurement_platform.workflows.orchestrator import WorkflowOrchestrator
from procurement_platform.persistence.lineage import get_executions_for_document, get_lineage_for_execution
from datetime import datetime, UTC


def test_lineage_recorded(db_session):
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id="req_lineage_1",
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
        source="test",
        created_at=datetime.now(UTC),
    )
    exec_obj = orch.create_execution(db_session, normalized=norm, trace_id="trace_lineage")
    exec_obj = orch.advance_synthetic(db_session, exec_obj.execution_id, trace_id="trace_lineage")
    # Check audit events have lineage
    from procurement_platform.persistence.models import AuditEventRow

    rows = db_session.query(AuditEventRow).filter(AuditEventRow.execution_id == exec_obj.execution_id).all()
    # At least one event should have lineage with supplier_ids
    has_supplier = False
    for r in rows:
        details = r.details or {}
        lineage = details.get("lineage")
        if lineage and lineage.get("supplier_ids"):
            has_supplier = True
            assert "supplier_demo" in lineage["supplier_ids"] or len(lineage["supplier_ids"]) > 0
    assert has_supplier

    # Query lineage for execution
    lineage = get_lineage_for_execution(db_session, exec_obj.execution_id)
    assert lineage["execution_id"] == exec_obj.execution_id
    assert "supplier_ids" in lineage
    assert len(lineage["supplier_ids"]) > 0

    # Query executions for policy (if any policy ids recorded)
    # Our proposal has policy_ids from applied policies
    # Find a policy_id from lineage
    if lineage["policy_ids"]:
        pid = lineage["policy_ids"][0]
        execs = get_executions_for_document(db_session, pid)  # may be 0 if policy not document?
        # At least check that function works without error
        assert isinstance(execs, list)
    # Also test supplier lineage
    sup_id = lineage["supplier_ids"][0]
    execs_sup = get_executions_for_document(db_session, sup_id)  # not document, but supplier
    # Use supplier specific function
    from procurement_platform.persistence.lineage import get_executions_for_supplier

    execs_s = get_executions_for_supplier(db_session, sup_id)
    assert any(e["execution_id"] == exec_obj.execution_id for e in execs_s)


def test_lineage_via_api(client, db_session):
    # Create execution via API
    resp = client.post("/v1/procurement/executions", json={"tenant_id": "tenant_demo", "requester_id": "user_01", "items": [{"sku": "MAT-001", "quantity": 10, "unit": "piece"}]})
    assert resp.status_code == 202
    exec_id = resp.json()["execution_id"]
    # Get lineage for execution
    resp2 = client.get(f"/v1/lineage?execution_id={exec_id}")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["execution_id"] == exec_id
    assert "supplier_ids" in data
    # Query by supplier
    sup_id = data["supplier_ids"][0] if data["supplier_ids"] else "supplier_demo"
    resp3 = client.get(f"/v1/lineage?supplier_id={sup_id}")
    assert resp3.status_code == 200
    assert resp3.json()["supplier_id"] == sup_id
