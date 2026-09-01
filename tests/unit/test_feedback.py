"""F4-5 Feedback loop tests."""

from datetime import UTC, datetime

from procurement_platform.rag.ingestion import IngestionPipeline
from procurement_platform.rag.models import Document, DocumentMetadata


def make_doc(doc_id="doc_fb", tenant="tenant_demo", content="Política presupuesto 5000"):
    meta = DocumentMetadata(
        document_id=doc_id,
        tenant_id=tenant,
        title="Test",
        doc_type="policy",
        classification="internal",
        jurisdiction="global",
        version="1.0.0",
        valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        status="approved",
        allowed_tenants=[tenant],
    )
    return Document(metadata=meta, content=content)


def test_feedback_record_and_boost(db_session):
    from procurement_platform.persistence.database import get_engine, Base
    from procurement_platform.persistence.models import DocumentChunkRow

    # ensure tables
    # use db_session fixture already has clean db
    pipeline = IngestionPipeline()
    doc = make_doc()
    _, chunks = pipeline.ingest(document=doc)
    assert chunks
    chunk_id = chunks[0].metadata.chunk_id

    # persist chunk via RagService
    from procurement_platform.rag.service import RagService

    rag = RagService(embedder=pipeline.embedder)
    # need to persist manually
    rag.ingest_document(document=doc, db=db_session)
    # verify row exists
    row = db_session.get(DocumentChunkRow, chunk_id)
    assert row is not None
    assert row.feedback_score == 0

    from procurement_platform.rag.feedback_store import record_feedback

    res = record_feedback(db_session, chunk_id=chunk_id, useful=True, actor_id="tester")
    assert res["feedback_score"] == 1.0
    assert res["feedback_count"] == 1
    db_session.refresh(row)
    assert row.feedback_score == 1.0

    # second feedback downvote
    res2 = record_feedback(db_session, chunk_id=chunk_id, useful=False, actor_id="tester2")
    assert res2["feedback_score"] == 0.0
    assert res2["feedback_count"] == 2

    # tenant mismatch should raise
    try:
        record_feedback(db_session, chunk_id=chunk_id, useful=True, tenant_id="other_tenant")
        assert False, "should have raised"
    except ValueError as e:
        assert "tenant mismatch" in str(e)


def test_feedback_api(client, db_session):
    from procurement_platform.rag.ingestion import IngestionPipeline

    pipeline = IngestionPipeline()
    doc = make_doc(
        doc_id="doc_api_fb", content="Política límite 5000 para feedback api test largo suficiente"
    )
    from procurement_platform.rag.service import RagService

    rag = RagService(embedder=pipeline.embedder)
    status, chunks = rag.ingest_document(document=doc, db=db_session)
    assert status == "indexed"
    cid = chunks[0].metadata.chunk_id

    # need to ensure retrieval has chunk for boosting check, but we can just call API
    resp = client.post("/v1/rag/feedback", json={"chunk_id": cid, "useful": True})
    assert resp.status_code == 200
    data = resp.json()
    assert data["chunk_id"] == cid
    assert data["feedback_score"] == 1.0

    # get stats
    resp2 = client.get("/v1/rag/feedback", params={"chunk_id": cid})
    assert resp2.status_code == 200
    assert resp2.json()["feedback_count"] == 1

    # list top
    resp3 = client.get("/v1/rag/feedback", params={"tenant_id": "tenant_demo", "limit": 5})
    assert resp3.status_code == 200
    assert "results" in resp3.json()
