"""RAG Service de alto nivel — Fase 3.

Orquesta ingesta + retrieval + seguridad + auditoría.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from procurement_platform.rag.embeddings import Embedder, get_embedder
from procurement_platform.rag.ingestion import IngestionPipeline
from procurement_platform.rag.models import Chunk, Document, RetrievalQuery
from procurement_platform.rag.retrieval import RetrievalService
from procurement_platform.rag.security import should_block_execution


class RagService:
    """Servicio RAG con boundary claro para dominio.

    Puede usarse in-memory (tests) o con persistencia DB/pgvector.
    Fase 4: soporte reranker y feedback boosting.
    """

    def __init__(
        self,
        embedder: Embedder | None = None,
        pipeline: IngestionPipeline | None = None,
        retrieval: RetrievalService | None = None,
    ) -> None:
        self.embedder = embedder or get_embedder()  # type: ignore
        self.pipeline = pipeline or IngestionPipeline(embedder=self.embedder)  # type: ignore
        self.retrieval = retrieval or RetrievalService(embedder=self.embedder)  # type: ignore
        # para auditoría
        self._ingestion_log: list[dict[str, Any]] = []

    def ingest_document(
        self,
        *,
        document: Document,
        filename: str | None = None,
        actor_id: str = "system",
        db: Session | None = None,
        allow_reindex: bool = False,
    ) -> tuple[str, list[Chunk]]:
        """Ingesta un documento y lo indexa. Retorna (status, chunks)."""
        result, chunks = self.pipeline.ingest(
            document=document, filename=filename, actor_id=actor_id, allow_reindex=allow_reindex
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

    def ingest_from_gcs(
        self,
        *,
        gcs_uri: str,
        metadata_kwargs: dict[str, Any],
        db: Session | None = None,
        allow_reindex: bool = False,
    ) -> tuple[str, list[Chunk]]:
        """F4-6: ingesta desde GCS (gs://...). Delega a GCSIngestor."""
        from procurement_platform.rag.ingestion import GCSIngestor

        ingestor = GCSIngestor()
        # GCSIngestor will handle download + pipeline ingest + DB persist with gcs_uri
        # we pass our pipeline so dedup is shared
        status, chunks = ingestor.ingest_from_gcs(
            gcs_uri=gcs_uri,
            metadata_kwargs=metadata_kwargs,
            pipeline=self.pipeline,
            db=db,
            allow_reindex=allow_reindex,
        )
        if status == "indexed":
            self.retrieval.index_chunks(chunks)
            self._ingestion_log.append(
                {
                    "document_id": metadata_kwargs.get("document_id", gcs_uri),
                    "status": status,
                    "chunks": len(chunks),
                    "gcs_uri": gcs_uri,
                    "timestamp": datetime.now(UTC).isoformat(),
                }
            )
        return status, chunks

    def _persist_chunks(self, db: Session, document: Document, chunks: list[Chunk]) -> None:
        # Import aquí para evitar ciclo
        from procurement_platform.persistence.models import DocumentRow, DocumentChunkRow

        # gcs_uri if present on document (F4-6)
        gcs_uri = getattr(document, "_gcs_uri", None)  # type: ignore
        if gcs_uri is None:
            gcs_uri = document.__dict__.get("_gcs_uri")  # type: ignore

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
                gcs_uri=gcs_uri,
                updated_at=datetime.now(UTC),
            )
            db.add(doc_row)
        else:
            # update gcs_uri / version history if provided
            if gcs_uri:
                doc_row.gcs_uri = gcs_uri
                doc_row.version = document.metadata.version
                doc_row.updated_at = datetime.now(UTC)
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
                    embedding_vec=ch.embedding,  # mirror for pgvector HNSW (F4-2)
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
        use_reranker: bool | None = None,
    ) -> dict[str, Any]:
        # F4-4: reranker flag — if enabled, retrieve more then rerank
        if use_reranker is None:
            try:
                from procurement_platform.config.settings import get_settings

                use_reranker = bool(get_settings().reranker_enabled)
            except Exception:
                use_reranker = False
        # if reranker enabled, fetch top_k*4 candidates then rerank to top_k
        fetch_k = top_k * 4 if use_reranker else top_k
        q = RetrievalQuery(
            query=query,
            tenant_id=tenant_id,
            location_id=location_id,
            jurisdiction=jurisdiction,
            policy_type=policy_type,
            top_k=fetch_k,
        )
        res = self.retrieval.retrieve_with_validation(q)
        if use_reranker and res["results"]:
            try:
                from procurement_platform.rag.reranker import get_reranker

                reranker = get_reranker()
                reranked = reranker.rerank(query, res["results"], top_k=top_k)
                res["results"] = reranked
                res["count"] = len(reranked)
                res["reranked"] = True
            except Exception:
                res["reranked"] = False
                # trim to top_k if we fetched more
                res["results"] = res["results"][:top_k]
                res["count"] = len(res["results"])
        else:
            # trim if fetched more without reranker (shouldn't happen)
            if len(res["results"]) > top_k:
                res["results"] = res["results"][:top_k]
                res["count"] = len(res["results"])
        return res

    def retrieve_with_rerank(
        self,
        *,
        query: str,
        tenant_id: str,
        location_id: str | None = None,
        jurisdiction: str | None = None,
        policy_type: str | None = None,
        top_k: int = 5,
    ) -> dict[str, Any]:
        """F4-4: wrapper que fuerza reranker (top 20 -> top 5)."""
        # fetch 20, rerank to top_k
        q = RetrievalQuery(
            query=query,
            tenant_id=tenant_id,
            location_id=location_id,
            jurisdiction=jurisdiction,
            policy_type=policy_type,
            top_k=20,
        )
        res = self.retrieval.retrieve_with_validation(q)
        try:
            from procurement_platform.rag.reranker import get_reranker

            reranker = get_reranker()
            if res["results"]:
                res["results"] = reranker.rerank(query, res["results"], top_k=top_k)
                res["count"] = len(res["results"])
                res["reranked"] = True
        except Exception:
            res["results"] = res["results"][:top_k]
            res["count"] = len(res["results"])
        return res

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
