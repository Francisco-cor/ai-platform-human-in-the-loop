"""Outbox drainer tests — F2-5."""

from procurement_platform.audit.service import create_audit_event
from procurement_platform.audit.drainer import drain_outbox
from procurement_platform.persistence.models import OutboxEvent


def test_audit_creates_outbox_transactionally(db_session):
    # create audit — should also create outbox in same flush
    create_audit_event(
        db_session,
        execution_id="exec_outbox_test",
        request_id="req_outbox",
        event_type="test.event",
        actor_type="system",
        actor_id="test",
        details={"foo": "bar"},
    )
    db_session.commit()
    # audit exists
    from procurement_platform.persistence.models import AuditEventRow

    audits = db_session.query(AuditEventRow).filter(AuditEventRow.execution_id == "exec_outbox_test").all()
    assert len(audits) == 1
    # outbox exists unprocessed
    outs = db_session.query(OutboxEvent).filter(OutboxEvent.aggregate_id == "exec_outbox_test").all()
    assert len(outs) == 1
    assert outs[0].processed_at is None
    assert outs[0].payload["event_type"] == "test.event"


def test_drain_outbox_marks_processed(db_session):
    create_audit_event(
        db_session,
        execution_id="exec_drain",
        request_id="req_drain",
        event_type="test.drain",
        actor_type="system",
        actor_id="test",
        details={"x": 1},
    )
    db_session.commit()
    # drain
    result = drain_outbox(db_session, batch=10)
    assert result["processed"] >= 1
    # verify processed
    outs = db_session.query(OutboxEvent).filter(OutboxEvent.aggregate_id == "exec_drain").all()
    assert all(o.processed_at is not None for o in outs)
