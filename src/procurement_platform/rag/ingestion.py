"""Pipeline de ingesta RAG — Fase 3 (§10).

Pasos: hash/dedup, malware scan stub, extracción, clasificación, separación normativo/no confiable,
fragmentación, embeddings, registro.
"""
from __future__ import annotations

import hashlib
from typing import Any

from procurement_platform.rag.embeddings import FakeEmbedder, get_embedder
from procurement_platform.rag.models import Chunk, ChunkMetadata, Document, IngestionResult
from procurement_platform.rag.security import classify_content, detect_prompt_injection


def compute_content_hash(content: str) -> str:
    return "sha256:" + hashlib.sha256(content.encode("utf-8")).hexdigest()


def scan_malware_stub(content: str, filename: str | None = None) -> dict[str, Any]:
    """Stub para scan malware — Fase 3: detecta archivos no permitidos por extensión."""
    allowed_extensions = {".pdf", ".txt", ".md", ".docx"}
    if filename:
        ext = "." + filename.split(".")[-1].lower() if "." in filename else ""
        if ext and ext not in allowed_extensions:
            return {"is_clean": False, "reason": f"extensión no permitida {ext}", "flags": ["blocked_extension"]}
    # heurística simple: si contenido contiene binary marker
    if "\x00" in content:
        return {"is_clean": False, "reason": "contenido binario sospechoso", "flags": ["binary_content"]}
    return {"is_clean": True, "reason": "ok", "flags": []}


def extract_text_preserving_pages(content: str, pages: list[dict[str, Any]] | None = None) -> tuple[str, list[dict[str, Any]]]:
    """Extrae texto preservando páginas/secciones. Fase 3: stub que preserva si ya vienen páginas."""
    if pages:
        return content, pages
    # si no hay páginas, fragmentar por doble salto de línea como simulación de páginas
    raw_pages = []
    for i, page_text in enumerate(content.split("\n\n"), start=1):
        raw_pages.append({"page": i, "section": f"section_{i}", "text": page_text.strip()})
    return content, raw_pages


def chunk_text(text: str, chunk_size: int = 500, overlap: int = 50) -> list[str]:
    """Fragmentación simple por tamaño con solapamiento."""
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + chunk_size, len(text))
        chunk = text[start:end].strip()
        if chunk:
            chunks.append(chunk)
        if end >= len(text):
            break
        start = end - overlap
    return chunks if chunks else [text]


class IngestionPipeline:
    """Pipeline de ingesta con defensa multicapa."""

    def __init__(self, embedder: FakeEmbedder | None = None, chunk_size: int = 500, chunk_overlap: int = 50) -> None:
        self.embedder = embedder or get_embedder()
        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap
        # in-memory dedup store (en producción sería DB/GCS)
        self._seen_hashes: set[str] = set()

    def ingest(
        self,
        *,
        document: Document,
        filename: str | None = None,
        actor_id: str = "system",
    ) -> tuple[IngestionResult, list[Chunk]]:
        # 1. hash y dedup
        content_hash = compute_content_hash(document.content)
        document.metadata.content_hash = content_hash
        if content_hash in self._seen_hashes:
            return IngestionResult(document_id=document.metadata.document_id, status="duplicate", chunks_created=0, content_hash=content_hash, reason="hash duplicado"), []
        # 2. malware scan
        scan = scan_malware_stub(document.content, filename)
        if not scan["is_clean"]:
            document.metadata.security_flags = scan["flags"]
            return IngestionResult(document_id=document.metadata.document_id, status="rejected", chunks_created=0, content_hash=content_hash, security_flags=scan["flags"], reason=scan["reason"]), []
        # 3. extracción
        full_text, pages = extract_text_preserving_pages(document.content, document.pages)
        # 4. detección injection sobre documento completo
        injection = detect_prompt_injection(full_text)
        classification = classify_content(full_text, metadata=document.metadata.model_dump())
        is_malicious = classification["is_malicious"]
        reliability = classification["reliability"]
        security_flags = classification["flags"]
        # si es malicioso, no se indexa para decisiones automáticas pero se conserva evidencia
        if is_malicious:
            # Fase 3: marcar y conservar evidencia, impedir que se convierta en instrucción
            # Lo cuarentenamos: no genera chunks indexables, pero se registra
            document.metadata.security_flags = security_flags
            document.metadata.is_malicious = True
            document.metadata.content_hash = content_hash
            return IngestionResult(document_id=document.metadata.document_id, status="quarantined", chunks_created=0, content_hash=content_hash, security_flags=security_flags, reason="prompt_injection_detectado"), []

        # 5. fragmentación con metadata
        raw_chunks = chunk_text(full_text, chunk_size=self.chunk_size, overlap=self.chunk_overlap)
        chunks: list[Chunk] = []
        for idx, chunk_text_content in enumerate(raw_chunks):
            # por chunk, reclasificar (defensa en profundidad)
            chunk_class = classify_content(chunk_text_content)
            chunk_is_malicious = chunk_class["is_malicious"]
            chunk_reliability = chunk_class["reliability"]
            chunk_flags = chunk_class["flags"]
            # si chunk malicioso, lo marcamos pero lo excluimos de índice confiable
            if chunk_is_malicious:
                continue
            # metadata
            meta = ChunkMetadata(
                chunk_id=f"{document.metadata.document_id}_chk_{idx}",
                document_id=document.metadata.document_id,
                tenant_id=document.metadata.tenant_id,
                chunk_index=idx,
                section=pages[idx % len(pages)].get("section") if pages else f"section_{idx}",
                page=pages[idx % len(pages)].get("page") if pages else idx + 1,
                version=document.metadata.version,
                valid_from=document.metadata.valid_from,
                valid_to=document.metadata.valid_to,
                classification=document.metadata.classification,
                jurisdiction=document.metadata.jurisdiction,
                location_id=document.metadata.location_id,
                status=document.metadata.status,
                policy_type=document.metadata.doc_type.value if document.metadata.doc_type else None,
                reliability=chunk_reliability,  # type: ignore
                is_malicious=False,
                security_flags=chunk_flags,
            )
            # embedding
            emb = self.embedder.embed(chunk_text_content)
            chunk = Chunk(metadata=meta, text=chunk_text_content, embedding=emb, embedding_model=self.embedder.model_name)
            chunks.append(chunk)

        # registrar hash
        self._seen_hashes.add(content_hash)
        # actualizar metadata
        document.metadata.content_hash = content_hash
        document.metadata.security_flags = security_flags

        return IngestionResult(document_id=document.metadata.document_id, status="indexed", chunks_created=len(chunks), content_hash=content_hash, security_flags=security_flags), chunks

    def reset(self) -> None:
        self._seen_hashes.clear()
