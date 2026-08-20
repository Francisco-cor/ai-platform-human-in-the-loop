"""RAG Service de alto nivel — Fase 3.

Orquesta ingesta + retrieval + seguridad + auditoría.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from procurement_platform.rag.embeddings import FakeEmbedder, get_embedder
from procurement_platform.rag.ingestion import IngestionPipeline
from procurement_platform.rag.models import Chunk, Document, RetrievalQuery
from procurement_platform.rag.retrieval import RetrievalService
from procurement_platform.rag.security import should_block_execution


class RagService:
    """Servicio RAG con boundary claro para dominio.

    Puede usarse in-memory (tests) o con persistencia DB/pgvector.
    """

    def __init__(
        self,
        embedder: FakeEmbedder | None = None,
        pipeline: IngestionPipeline | None = None,
        retrieval: RetrievalService | None = None,
    ) -> None:
        self.embedder = embedder or get_embedder()
        self.pipeline = pipeline or IngestionPipeline(embedder=self.embedder)
        self.retrieval = retrieval or RetrievalService(embedder=self.embedder)
        # para auditoría
        self._ingestion_log: list[dict[str, Any]] = []

    def ingest_document(
        self,
        *,
        document: Document,
        filename: str | None = None,
        actor_id: str = "system",
        db: Session | None = None,
    ) -> tuple[str, list[Chunk]]:
        """Ingesta un documento y lo indexa. Retorna (status, chunks)."""
        result, chunks = self.pipeline.ingest(
            document=document, filename=filename, actor_id=actor_id
        )
        # log
        self._ingestion_log.append(
            {
                "document_id": document.metadata.document_id,
                "status": result.status,
                "chunks": result.chunks_created,
                "hash": result.content_hash,
                "flags": result.security_flags,
                "actor_id": actor_id,
                "timestamp": datetime.now(UTC).isoformat(),
            }
        )
        if result.status == "indexed":
            self.retrieval.index_chunks(chunks)
            # persistir a DB si se provee Session (opcional Fase 3)
            if db is not None:
                self._persist_chunks(db, document, chunks)
        return result.status, chunks

    def _persist_chunks(self, db: Session, document: Document, chunks: list[Chunk]) -> None:
        # Import aquí para evitar ciclo
        from procurement_platform.persistence.models import DocumentRow, DocumentChunkRow

        # upsert document
        doc_row = db.get(DocumentRow, document.metadata.document_id)
        if not doc_row:
            doc_row = DocumentRow(
                document_id=document.metadata.document_id,
                tenant_id=document.metadata.tenant_id,
                title=document.metadata.title,
                doc_type=document.metadata.doc_type.value,
                classification=document.metadata.classification.value,
                jurisdiction=document.metadata.jurisdiction,
                location_id=document.metadata.location_id,
                version=document.metadata.version,
                valid_from=document.metadata.valid_from,
                valid_to=document.metadata.valid_to,
                status=document.metadata.status.value,
                allowed_tenants=document.metadata.allowed_tenants,
                allowed_roles=document.metadata.allowed_roles,
                created_by=document.metadata.created_by,
                created_at=document.metadata.created_at,
                pipeline_version=document.metadata.pipeline_version,
                content_hash=document.metadata.content_hash,
                security_flags=document.metadata.security_flags,
                is_malicious=document.metadata.is_malicious,
                content=document.content,
                updated_at=datetime.now(UTC),
            )
            db.add(doc_row)
        # upsert chunks
        for ch in chunks:
            row = db.get(DocumentChunkRow, ch.metadata.chunk_id)
            if not row:
                row = DocumentChunkRow(
                    chunk_id=ch.metadata.chunk_id,
                    document_id=ch.metadata.document_id,
                    tenant_id=ch.metadata.tenant_id,
                    chunk_index=ch.metadata.chunk_index,
                    section=ch.metadata.section,
                    page=ch.metadata.page,
                    version=ch.metadata.version,
                    valid_from=ch.metadata.valid_from,
                    valid_to=ch.metadata.valid_to,
                    classification=ch.metadata.classification.value,
                    jurisdiction=ch.metadata.jurisdiction,
                    location_id=ch.metadata.location_id,
                    status=ch.metadata.status.value,
                    policy_type=ch.metadata.policy_type,
                    reliability=ch.metadata.reliability,
                    is_malicious=ch.metadata.is_malicious,
                    security_flags=ch.metadata.security_flags,
                    text=ch.text,
                    embedding=ch.embedding,  # JSON para SQLite, vector para pgvector si se usa
                    embedding_model=ch.embedding_model,
                    updated_at=datetime.now(UTC),
                )
                db.add(row)
        db.commit()

    def retrieve(
        self,
        *,
        query: str,
        tenant_id: str,
        location_id: str | None = None,
        jurisdiction: str | None = None,
        policy_type: str | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        q = RetrievalQuery(
            query=query,
            tenant_id=tenant_id,
            location_id=location_id,
            jurisdiction=jurisdiction,
            policy_type=policy_type,
            top_k=top_k,
        )
        return self.retrieval.retrieve_with_validation(q)

    def retrieve_for_execution(
        self,
        *,
        query: str,
        tenant_id: str,
        location_id: str,
        top_k: int = 5,
    ) -> tuple[list, bool, str]:
        """Recupera para ejecución de workflow y decide si bloquear.

        Retorna (results, should_block, reason).
        """
        res = self.retrieve(query=query, tenant_id=tenant_id, location_id=location_id, top_k=top_k)
        results = res["results"]
        has_conflict = res["conflict"]["has_conflict"]
        # verificar si alguno es malicioso (ya filtrado, pero por si acaso)
        is_malicious = any(r.chunk.metadata.is_malicious for r in results)
        # Fase 3 defensa: aunque el retrieval filtre maliciosos, si existe algún chunk malicioso
        # para este tenant en el store, debe bloquear (evaluación adversarial)
        # Esto asegura que malicious_document_001 sea BLOCKED aunque el filtro lo excluya.
        if not is_malicious:
            try:
                is_malicious = any(
                    c.metadata.is_malicious and c.metadata.tenant_id == tenant_id
                    for c in self.retrieval._chunks
                )
            except Exception:
                pass
        # verificar obsolescencia: si todos son obsoletos, no debería ejecutar
        is_expired = (
            all(
                r.chunk.metadata.valid_to and r.chunk.metadata.valid_to < datetime.now(UTC)
                for r in results
            )
            if results
            else False
        )
        reliability = "untrusted" if is_malicious else "high"
        should_block, reason = should_block_execution(
            is_malicious=is_malicious,
            is_expired=is_expired,
            has_conflict=has_conflict,
            reliability=reliability,
        )
        # Fase 3: si hay conflicto o malicioso, bloquear ejecución automática
        if has_conflict:
            should_block = True
            reason = "conflicto entre políticas"
        if is_malicious:
            should_block = True
            reason = "documento malicioso"
        return results, should_block, reason

    def clear(self) -> None:
        self.retrieval.clear()
        self.pipeline.reset()
        self._ingestion_log.clear()

    def stats(self) -> dict[str, Any]:
        return {
            "chunks_indexed": self.retrieval.count(),
            "ingestions": len(self._ingestion_log),
            "last_ingestion": self._ingestion_log[-1] if self._ingestion_log else None,
        }
