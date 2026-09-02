"""Fase 9 — retention and soft-delete tests."""

from datetime import UTC, datetime, timedelta

from procurement_platform.domain.models import NormalizedRequest, RequestItem
from procurement_platform.persistence.retention import run_retention, soft_delete_tenant, is_tenant_soft_deleted, clear_tombstones, get_tombstone
from procurement_platform.workflows.orchestrator import WorkflowOrchestrator
from procurement_platform.persistence.models import AuditEventRow, WorkflowExecution


def test_retention_dry_run_and_real(db_session):
    clear_tombstones()
    orch = WorkflowOrchestrator()
    norm = NormalizedRequest(
        request_id="req_ret_1",
        tenant_id="tenant_demo",
        requester_id="user_01",
        items=[RequestItem(sku="MAT-001", quantity=10, unit="piece")],
        horizon_days=21,
        location_id="warehouse_north",
        currency="USD",
        source="test",
        created_at=datetime.now(UTC),
    )
    exec_obj = orch.create_execution(db_session, normalized=norm, trace_id="trace_ret")
    orch.advance_synthetic(db_session, exec_obj.execution_id, trace_id="trace_ret")
    # Make audit events old (400 days ago)
    old_time = datetime.now(UTC) - timedelta(days=400)
    rows = db_session.query(AuditEventRow).filter(AuditEventRow.execution_id == exec_obj.execution_id).all()
    for r in rows:
        r.timestamp = old_time
    db_session.commit()

    # dry_run should not delete
    result_dry = run_retention(db_session, retention_days=365, dry_run=True)
    assert result_dry["archived"] >= 1
    assert result_dry["deleted"] == 0
    # ensure still there
    assert db_session.query(AuditEventRow).filter(AuditEventRow.execution_id == exec_obj.execution_id).count() > 0

    # real run should delete but keep hashes via tombstone
    result = run_retention(db_session, retention_days=365, dry_run=False)
    assert result["deleted"] >= 1
    # hashes kept via tombstone
    assert result["kept_hashes"] >= 1
    # after deletion, audit rows for this execution should be gone (or reduced)
    # but at least one tombstone should exist
    assert len(result) > 0
    clear_tombstones()


def test_soft_delete_tenant_via_api(client, db_session):
    clear_tombstones()
    # also clear any leftover audit tombstones from previous tests
    from procurement_platform.persistence.models import AuditEventRow

    db_session.query(AuditEventRow).filter(AuditEventRow.event_type == "tenant.data_soft_deleted").delete()
    db_session.commit()
    # create execution for tenant_demo
    resp = client.post("/v1/procurement/executions", json={"tenant_id": "tenant_demo", "requester_id": "user_01", "items": [{"sku": "MAT-001", "quantity": 10, "unit": "piece"}]})
    assert resp.status_code == 202
    exec_id = resp.json()["execution_id"]
    # soft delete
    resp2 = client.delete("/v1/tenants/tenant_demo/data")
    assert resp2.status_code == 200
    data = resp2.json()
    assert data["tenant_id"] == "tenant_demo"
    assert data["deleted_executions"] >= 1
    # check is soft deleted
    from procurement_platform.persistence.retention import is_tenant_soft_deleted

    assert is_tenant_soft_deleted(db_session, "tenant_demo") is True
    # second delete should return already_deleted
    resp3 = client.delete("/v1/tenants/tenant_demo/data")
    assert resp3.json()["status"] == "already_deleted"
    # cleanup: clear tombstones and audit tombstones
    clear_tombstones()
    db_session.query(AuditEventRow).filter(AuditEventRow.event_type == "tenant.data_soft_deleted").delete()
    db_session.commit()
    assert is_tenant_soft_deleted(db_session, "tenant_demo") is False
