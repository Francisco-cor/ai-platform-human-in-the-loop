"""RLS helper tests — F3-2."""

import pytest
from sqlalchemy.orm import Session

from procurement_platform.persistence.rls import apply_tenant_filter, assert_tenant_row_access, tenant_scoped_query
from procurement_platform.persistence.models import WorkflowExecution
from procurement_platform.domain.models import new_id, utcnow
from procurement_platform.domain.models import ExecutionState


def test_apply_tenant_filter(db_session: Session):
    # create two executions different tenants
    from procurement_platform.persistence.database import Base

    # clean
    db_session.query(WorkflowExecution).delete()
    db_session.commit()
    for tenant in ("tenant_demo", "tenant_other"):
        row = WorkflowExecution(
            execution_id=new_id("exec"),
            request_id=new_id("req"),
            tenant_id=tenant,
            status=ExecutionState.RECEIVED.value,
            current_node="intake",
            normalized_request=None,
            proposal=None,
            approval_request=None,
            trace_id="trace_test",
            created_at=utcnow(),
            updated_at=utcnow(),
        )
        db_session.add(row)
    db_session.commit()
    q = db_session.query(WorkflowExecution)
    filtered = apply_tenant_filter(q, WorkflowExecution, "tenant_demo").all()
    assert all(r.tenant_id == "tenant_demo" for r in filtered)
    assert len(filtered) == 1


def test_tenant_scoped_query(db_session: Session):
    db_session.query(WorkflowExecution).delete()
    db_session.commit()
    row = WorkflowExecution(
        execution_id=new_id("exec"),
        request_id=new_id("req"),
        tenant_id="tenant_demo",
        status=ExecutionState.RECEIVED.value,
        current_node="intake",
        normalized_request=None,
        proposal=None,
        approval_request=None,
        trace_id="trace_test",
        created_at=utcnow(),
        updated_at=utcnow(),
    )
    db_session.add(row)
    db_session.commit()
    rows = tenant_scoped_query(db_session, WorkflowExecution, "tenant_demo").all()
    assert len(rows) == 1
    rows_other = tenant_scoped_query(db_session, WorkflowExecution, "tenant_other").all()
    assert len(rows_other) == 0


def test_assert_tenant_row_access():
    # same tenant ok
    assert_tenant_row_access("tenant_demo", "tenant_demo")
    # cross tenant raises
    with pytest.raises(Exception) as exc:
        assert_tenant_row_access("tenant_other", "tenant_demo")
    assert "tenant_forbidden" in str(exc.value)
