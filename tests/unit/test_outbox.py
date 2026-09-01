"""Outbox transactional tests — F2-1."""

from procurement_platform.domain.models import new_id, utcnow
from procurement_platform.persistence.models import OutboxEvent


def test_outbox_insert_and_drain(db_session):
    # insert
    evt = OutboxEvent(
        event_id=new_id("evt"),
        aggregate_id="exec_test",
        event_type="execution.created",
        payload={"foo": "bar"},
        created_at=utcnow(),
        processed_at=None,
        attempts=0,
    )
    db_session.add(evt)
    db_session.commit()

    # query unprocessed
    rows = db_session.query(OutboxEvent).filter(OutboxEvent.processed_at.is_(None)).all()
    assert len(rows) >= 1
    # mark processed
    evt.processed_at = utcnow()
    db_session.commit()
    remaining = db_session.query(OutboxEvent).filter(OutboxEvent.processed_at.is_(None)).all()
    # should be less than before (at least evt removed)
    assert any(r.event_id == evt.event_id for r in rows)
    assert evt.event_id not in {r.event_id for r in remaining}


def test_outbox_attempts_increment(db_session):
    evt = OutboxEvent(
        event_id=new_id("evt"),
        aggregate_id="exec_2",
        event_type="tool.called",
        payload={"x": 1},
        created_at=utcnow(),
        attempts=0,
        last_error=None,
    )
    db_session.add(evt)
    db_session.commit()
    evt.attempts += 1
    evt.last_error = "timeout"
    db_session.commit()
    fetched = db_session.get(OutboxEvent, evt.event_id)
    assert fetched.attempts == 1
    assert fetched.last_error == "timeout"
