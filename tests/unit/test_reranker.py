"""F4-4 CrossEncoder reranker tests."""

from datetime import UTC, datetime

from procurement_platform.rag.ingestion import IngestionPipeline
from procurement_platform.rag.models import Document, DocumentMetadata, RetrievalQuery
from procurement_platform.rag.reranker import CrossEncoderReranker, get_reranker, reset_reranker
from procurement_platform.rag.retrieval import RetrievalService


def make_doc(doc_id, tenant, content):
    meta = DocumentMetadata(
        document_id=doc_id,
        tenant_id=tenant,
        title=f"Doc {doc_id}",
        doc_type="policy",
        classification="internal",
        jurisdiction="global",
        version="1.0.0",
        valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        status="approved",
        allowed_tenants=[tenant],
    )
    return Document(metadata=meta, content=content)


def test_reranker_heuristic():
    r = CrossEncoderReranker()
    s = r.score("presupuesto límite 5000", "Política: límite delegado 5000 USD presupuesto")
    assert 0 <= s <= 1
    s2 = r.score("presupuesto límite 5000", "Manual usuario login")
    assert s > s2


def test_rerank_order():
    pipeline = IngestionPipeline()
    retrieval = RetrievalService(embedder=pipeline.embedder)
    docs = [
        make_doc("doc_budget", "tenant_demo", "Política límite 5000 USD presupuesto"),
        make_doc("doc_irr", "tenant_demo", "Manual usuario login sistema"),
        make_doc("doc_supplier", "tenant_demo", "Política proveedores supplier_demo"),
    ]
    for doc in docs:
        _, chunks = pipeline.ingest(document=doc)
        retrieval.index_chunks(chunks)
    q = RetrievalQuery(query="presupuesto límite", tenant_id="tenant_demo", top_k=5)
    results = retrieval.retrieve(q)
    reranker = CrossEncoderReranker()
    reranked = reranker.rerank("presupuesto límite", results, top_k=2)
    assert len(reranked) == 2
    for r in reranked:
        assert "rerank_score" in r.citation
        assert r.citation["rerank_score"] is not None
        assert "citation_verified" in r.citation


def test_verify_citation():
    pipeline = IngestionPipeline()
    retrieval = RetrievalService(embedder=pipeline.embedder)
    doc = make_doc("doc_cite", "tenant_demo", "Política límite 5000 § sección budget")
    _, chunks = pipeline.ingest(document=doc)
    retrieval.index_chunks(chunks)
    q = RetrievalQuery(query="límite presupuestario", tenant_id="tenant_demo", top_k=1)
    results = retrieval.retrieve(q)
    r = CrossEncoderReranker()
    # ensure rerank to get citation fields
    reranked = r.rerank("límite presupuestario", results, top_k=1)
    cit = reranked[0].citation
    checks = r.verify_citation(reranked[0])
    assert checks["has_document_id"] is True
    assert checks["has_version"] is True
    assert cit["document_id"] == "doc_cite"


def test_get_reranker_singleton():
    reset_reranker()
    a = get_reranker()
    b = get_reranker()
    assert a is b
    reset_reranker()
