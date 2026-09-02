"""
Platform RAG — generic secure retrieval (Fase 11).

Ingestion, chunking, embedding (fake/gemini 384), tenant/vigencia/jurisdiction
filters, quarantine for malicious, hybrid BM25+vector, reranker, feedback.

Domain-specific doc types (policy_budget, policy_supplier) are filtered by
caller, but core retrieval is generic.
"""

from __future__ import annotations

# Lazy to avoid importing domain
def get_rag_service():
    from procurement_platform.workflows.orchestrator import get_rag_service as _get

    return _get()


__all__ = ["get_rag_service"]
