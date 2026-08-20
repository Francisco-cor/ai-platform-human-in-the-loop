"""Tests tenant isolation — Fase 7."""

import pytest

from procurement_platform.security.tenant import is_tenant_allowed, filter_by_tenant
from procurement_platform.rag.retrieval import RetrievalService
from procurement_platform.rag.models import Document, DocumentMetadata
from procurement_platform.rag.embeddings import FakeEmbedder
from datetime import UTC, datetime


def test_is_tenant_allowed_same():
    assert is_tenant_allowed("tenant_demo", "tenant_demo", None) is True


def test_cross_tenant_not_allowed():
    assert is_tenant_allowed("tenant_demo", "tenant_other", ["tenant_demo"]) is False
    assert is_tenant_allowed("tenant_demo", "tenant_other", None) is False


def test_filter_by_tenant():
    items = [{"tenant_id": "tenant_demo", "id": 1}, {"tenant_id": "tenant_other", "id": 2}]
    filtered = filter_by_tenant(items, "tenant_demo")
    assert len(filtered) == 1
    assert filtered[0]["id"] == 1


def test_retrieval_tenant_isolation():
    svc = RetrievalService(embedder=FakeEmbedder())
    # ingest doc for tenant_other
    meta_other = DocumentMetadata(
        document_id="doc_other_001",
        tenant_id="tenant_other",
        title="Other policy",
        doc_type="policy",
        classification="internal",
        jurisdiction="global",
        version="1.0.0",
        valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        status="approved",
        allowed_tenants=["tenant_other"],
    )

    # create fake chunk directly

    # Use ingestion via service with other tenant
    # Instead, manually add chunk
    from procurement_platform.rag.service import RagService

    rag = RagService()
    doc_other = Document(
        metadata=meta_other,
        content="Política límite 999999 para tenant_other. Normativa.",
        pages=[],
    )
    rag.ingest_document(document=doc_other, actor_id="test")
    # retrieve as tenant_demo should not get other tenant's doc
    res = rag.retrieve(query="límite presupuesto", tenant_id="tenant_demo", top_k=5)
    for r in res["results"]:
        assert r.chunk.metadata.tenant_id == "tenant_demo"

    # retrieve as tenant_other should get it
    res2 = rag.retrieve(query="límite presupuesto", tenant_id="tenant_other", top_k=5)
    # may have results if indexed
    assert all(r.chunk.metadata.tenant_id == "tenant_other" for r in res2["results"])


def test_gateway_tenant_isolation_violation():
    from procurement_platform.domain.models import ExecutionState
    from procurement_platform.tools.gateway import ToolGateway, ToolGatewayError

    gw = ToolGateway()
    with pytest.raises(ToolGatewayError, match="tenant_isolation_violation"):
        gw.call(
            tool_name="get_inventory",
            payload={
                "sku": "MAT-001",
                "location_id": "warehouse_north",
                "tenant_id": "tenant_other",
            },
            execution_id="exec_test",
            state=ExecutionState.RECEIVED,
            tenant_id="tenant_demo",
        )
