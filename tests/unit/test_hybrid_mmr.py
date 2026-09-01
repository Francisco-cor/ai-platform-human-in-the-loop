"""F4-3 Hybrid retrieval BM25 + vector + MMR tests."""

from datetime import UTC, datetime

from procurement_platform.rag.ingestion import IngestionPipeline
from procurement_platform.rag.models import Document, DocumentMetadata, RetrievalQuery
from procurement_platform.rag.retrieval import RetrievalService, HybridRetriever


def make_doc(doc_id, tenant, content, version="1.0.0"):
    meta = DocumentMetadata(
        document_id=doc_id,
        tenant_id=tenant,
        title=f"Doc {doc_id}",
        doc_type="policy",
        classification="internal",
        jurisdiction="global",
        version=version,
        valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        status="approved",
        allowed_tenants=[tenant],
    )
    return Document(metadata=meta, content=content)


def test_hybrid_basic():
    pipeline = IngestionPipeline()
    retrieval = RetrievalService(embedder=pipeline.embedder)
    doc1 = make_doc(
        "doc_budget",
        "tenant_demo",
        "Política: límite delegado 5000 USD para warehouse_north. Presupuesto.",
    )
    doc2 = make_doc(
        "doc_supplier",
        "tenant_demo",
        "Política: proveedores permitidos supplier_demo y supplier_alt.",
    )
    for doc in (doc1, doc2):
        _, chunks = pipeline.ingest(document=doc)
        retrieval.index_chunks(chunks)
    q = RetrievalQuery(
        query="límite presupuestario 5000",
        tenant_id="tenant_demo",
        location_id="warehouse_north",
        top_k=5,
    )
    results = retrieval.retrieve(q)
    assert len(results) == 2
    ids = {r.chunk.metadata.document_id for r in results}
    assert "doc_budget" in ids and "doc_supplier" in ids
    for r in results:
        assert "vector_score" in r.citation
        assert "bm25_score" in r.citation
        assert r.citation["score"] is not None


def test_hybrid_bm25_boost():
    pipeline = IngestionPipeline()
    retrieval = RetrievalService(embedder=pipeline.embedder)
    # doc with query terms should rank higher even if vector random
    doc_rel = make_doc("doc_rel", "tenant_demo", "Política presupuesto límite 5000 USD delegado")
    doc_irr = make_doc("doc_irr", "tenant_demo", "Manual de usuario login sistema guía instalación")
    for doc in (doc_rel, doc_irr):
        _, chunks = pipeline.ingest(document=doc)
        retrieval.index_chunks(chunks)
    q = RetrievalQuery(query="presupuesto límite 5000", tenant_id="tenant_demo", top_k=5)
    results = retrieval.retrieve(q)
    # relevant should be first due to BM25 boost
    assert results[0].chunk.metadata.document_id == "doc_rel"


def test_mmr_diversity():
    pipeline = IngestionPipeline()
    retrieval = RetrievalService(embedder=pipeline.embedder)
    retrieval.mmr_lambda = 0.5
    # 3 docs, 2 very similar, 1 different
    doc_a = make_doc("doc_a", "tenant_demo", "Política límite 5000 USD presupuesto")
    doc_b = make_doc(
        "doc_b", "tenant_demo", "Política límite 5000 USD presupuesto"
    )  # same as a (duplicate content but different doc_id/content hash same -> dedup will block second)
    # to avoid dedup, make content slightly different but similar tokens
    doc_b = make_doc("doc_b", "tenant_demo", "Política límite 5000 USD presupuesto variante texto")
    doc_c = make_doc("doc_c", "tenant_demo", "Política proveedores permitidos supplier_demo")
    for doc in (doc_a, doc_b, doc_c):
        _, chunks = pipeline.ingest(document=doc)
        retrieval.index_chunks(chunks)
    q = RetrievalQuery(query="límite 5000", tenant_id="tenant_demo", top_k=2)
    results = retrieval.retrieve(q)
    assert len(results) == 2
    # MMR should still return 2 diverse
    assert len({r.chunk.metadata.document_id for r in results}) == 2


def test_hybrid_alias():
    hr = HybridRetriever()
    assert isinstance(hr, RetrievalService)


def test_feedback_boost():
    pipeline = IngestionPipeline()
    retrieval = RetrievalService(embedder=pipeline.embedder)
    doc1 = make_doc("doc1", "tenant_demo", "Política presupuesto 5000")
    doc2 = make_doc("doc2", "tenant_demo", "Política presupuesto 5000 variante")
    for doc in (doc1, doc2):
        _, chunks = pipeline.ingest(document=doc)
        retrieval.index_chunks(chunks)
    # without feedback, both similar
    q = RetrievalQuery(query="presupuesto 5000", tenant_id="tenant_demo", top_k=2)
    res1 = retrieval.retrieve(q)
    # give feedback to doc2 to boost it
    for ch in retrieval.get_all():
        if ch.metadata.document_id == "doc2":
            ch.metadata.__dict__["feedback_score"] = 5  # boost
    res2 = retrieval.retrieve(q)
    # doc2 should now rank higher (or at least score higher)
    # find scores
    scores_before = {r.chunk.metadata.document_id: r.score for r in res1}
    scores_after = {r.chunk.metadata.document_id: r.score for r in res2}
    # feedback boosted score should increase
    assert scores_after["doc2"] > scores_before["doc2"]
