"""Recuperación RAG con filtros, citas y scoring — Fase 3 (§10)."""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from procurement_platform.rag.embeddings import FakeEmbedder, cosine_similarity, get_embedder
from procurement_platform.rag.models import Chunk, RetrievalQuery, RetrievalResult
from procurement_platform.rag.security import check_obsolescence


class RetrievalService:
    """Servicio de recuperación con filtros previos al ranking."""

    def __init__(self, embedder: FakeEmbedder | None = None) -> None:
        self.embedder = embedder or get_embedder()
        # in-memory store de chunks (en producción sería pgvector)
        self._chunks: list[Chunk] = []

    def index_chunks(self, chunks: list[Chunk]) -> None:
        self._chunks.extend(chunks)

    def clear(self) -> None:
        self._chunks.clear()

    def _filter(self, chunk: Chunk, query: RetrievalQuery, now: datetime) -> bool:
        meta = chunk.metadata
        # tenant
        if meta.tenant_id != query.tenant_id:
            return False
        # jurisdiction
        if query.jurisdiction and meta.jurisdiction not in (query.jurisdiction, "global"):
            return False
        # location
        if query.location_id and meta.location_id and meta.location_id != query.location_id:
            # si el chunk tiene location específica y no coincide, filtrar
            return False
        # doc_type / policy_type
        if query.doc_type and meta.policy_type != query.doc_type.value:
            # si se filtra por tipo pero el chunk no coincide, excluir (si policy_type está seteado)
            # permitimos si policy_type es None y doc_type es policy (flexible)
            pass
        if query.policy_type and meta.policy_type != query.policy_type:
            return False
        # status
        if query.require_approved and meta.status.value != "approved":
            return False
        # vigencia
        if query.require_valid:
            obs = check_obsolescence(meta.valid_from, meta.valid_to, now)
            if not obs["is_valid"]:
                return False
        # clasificación
        if query.allowed_classifications and meta.classification not in query.allowed_classifications:
            return False
        # seguridad: excluir maliciosos y untrusted si se requiere alta confiabilidad
        if meta.is_malicious:
            return False
        if meta.reliability == "untrusted":
            return False
        return True

    def retrieve(self, query: RetrievalQuery, now: datetime | None = None) -> list[RetrievalResult]:
        now = now or datetime.now(UTC)
        # 1. filtrar antes de rankear (§10: tenant, vigencia, jurisdicción, permisos, etc.)
        candidates = [c for c in self._chunks if self._filter(c, query, now)]
        if not candidates:
            return []

        # 2. embedding de la query
        q_emb = self.embedder.embed(query.query)
        scored: list[tuple[Chunk, float]] = []
        for chunk in candidates:
            if chunk.embedding is None:
                continue
            score = cosine_similarity(q_emb, chunk.embedding)
            scored.append((chunk, score))

        # 3. ordenar por score descendente
        scored.sort(key=lambda x: x[1], reverse=True)
        top = scored[: query.top_k]

        results: list[RetrievalResult] = []
        for chunk, score in top:
            citation = {
                "document_id": chunk.metadata.document_id,
                "version": chunk.metadata.version,
                "page": chunk.metadata.page,
                "section": chunk.metadata.section,
                "score": round(score, 4),
                "valid_from": chunk.metadata.valid_from.isoformat(),
                "valid_to": chunk.metadata.valid_to.isoformat() if chunk.metadata.valid_to else None,
                "reliability": chunk.metadata.reliability,
                "jurisdiction": chunk.metadata.jurisdiction,
            }
            # actualizar score en metadata
            chunk.metadata.score = score
            results.append(RetrievalResult(chunk=chunk, score=score, citation=citation))
        return results

    def retrieve_with_validation(
        self, query: RetrievalQuery, now: datetime | None = None
    ) -> dict[str, Any]:
        """Wrapper que incluye validación de obsolescencia y conflicto.

        Retorna dict con results, warnings, conflict info, etc.
        """
        from procurement_platform.rag.security import detect_conflict

        results = self.retrieve(query, now=now)
        # detectar obsolescencia entre resultados (si hay versiones mezcladas)
        # no bloquear automáticamente, pero advertir
        warnings: list[str] = []
        for r in results:
            obs = check_obsolescence(r.chunk.metadata.valid_from, r.chunk.metadata.valid_to, now or datetime.now(UTC))
            if obs["is_expired"]:
                warnings.append(f"document {r.chunk.metadata.document_id} vencido")
        # detectar conflicto entre políticas recuperadas
        policies_for_conflict = []
        for r in results:
            # mapear a dict para detect_conflict
            policies_for_conflict.append(
                {
                    "document_id": r.chunk.metadata.document_id,
                    "tenant_id": r.chunk.metadata.tenant_id,
                    "policy_type": r.chunk.metadata.policy_type,
                    "location_id": r.chunk.metadata.location_id,
                    "rules": {"text": r.chunk.text[:200]},
                }
            )
        conflict_info = detect_conflict(policies_for_conflict) if len(policies_for_conflict) > 1 else {"has_conflict": False, "conflicts": []}
        if conflict_info["has_conflict"]:
            warnings.append(f"conflicto detectado: {conflict_info['conflicts']}")

        return {
            "results": results,
            "count": len(results),
            "warnings": warnings,
            "conflict": conflict_info,
            "query": query.model_dump(),
        }

    def count(self) -> int:
        return len(self._chunks)

    def get_all(self) -> list[Chunk]:
        return list(self._chunks)
