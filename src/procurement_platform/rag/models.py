"""Modelos Pydantic para RAG seguro — Fase 3 (§10).

Cada documento y chunk lleva metadata de tenant, vigencia, jurisdicción y permisos.
"""
from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Literal

from pydantic import BaseModel, Field


def utcnow() -> datetime:
    return datetime.now(UTC)


class DocumentStatus(str, Enum):
    draft = "draft"
    approved = "approved"
    retired = "retired"


class DocumentClassification(str, Enum):
    public = "public"
    internal = "internal"
    confidential = "confidential"
    restricted = "restricted"


class DocumentType(str, Enum):
    policy = "policy"
    procedure = "procedure"
    guideline = "guideline"
    contract = "contract"
    other = "other"


class DocumentMetadata(BaseModel):
    document_id: str
    tenant_id: str
    title: str
    source: str = Field(default="gcs", description="origen: gcs, manual, etc.")
    doc_type: DocumentType = DocumentType.policy
    classification: DocumentClassification = DocumentClassification.internal
    jurisdiction: str = Field(default="global", description="ej: MX, US, global")
    location_id: str | None = None
    version: str = Field(default="1.0.0")
    valid_from: datetime = Field(default_factory=utcnow)
    valid_to: datetime | None = None
    status: DocumentStatus = DocumentStatus.approved
    # permisos: lista de roles/tenants que pueden ver
    allowed_tenants: list[str] = Field(default_factory=list)
    allowed_roles: list[str] = Field(default_factory=list)
    created_by: str = Field(default="system")
    created_at: datetime = Field(default_factory=utcnow)
    pipeline_version: str = Field(default="rag-v1")
    # hash del contenido original para dedup
    content_hash: str | None = None
    # seguridad
    security_flags: list[str] = Field(default_factory=list)  # ej: injection_detected
    is_malicious: bool = False


class Document(BaseModel):
    metadata: DocumentMetadata
    content: str = Field(..., description="texto extraído, preservando páginas/secciones")
    # referencias de páginas/secciones si aplica
    pages: list[dict[str, Any]] = Field(default_factory=list)


class ChunkMetadata(BaseModel):
    chunk_id: str
    document_id: str
    tenant_id: str
    chunk_index: int
    section: str | None = None
    page: int | None = None
    version: str
    valid_from: datetime
    valid_to: datetime | None
    classification: DocumentClassification
    jurisdiction: str
    location_id: str | None
    status: DocumentStatus
    # para retrieval
    policy_type: str | None = None  # ej: budget_limit, supplier_allowlist
    score: float | None = None
    reliability: Literal["high", "medium", "low", "untrusted"] = "high"

    # seguridad
    is_malicious: bool = False
    security_flags: list[str] = Field(default_factory=list)


class Chunk(BaseModel):
    metadata: ChunkMetadata
    text: str
    embedding: list[float] | None = None
    embedding_model: str = Field(default="fake-384")


class RetrievalQuery(BaseModel):
    query: str
    tenant_id: str
    location_id: str | None = None
    jurisdiction: str | None = None
    doc_type: DocumentType | None = None
    policy_type: str | None = None
    top_k: int = Field(default=5, ge=1, le=20)
    # filtros
    require_approved: bool = True
    require_valid: bool = True
    allowed_classifications: list[DocumentClassification] | None = None


class RetrievalResult(BaseModel):
    chunk: Chunk
    score: float
    citation: dict[str, Any] = Field(default_factory=dict)  # document_id, version, page/section, score

    @property
    def is_reliable(self) -> bool:
        return self.chunk.metadata.reliability in {"high", "medium"} and not self.chunk.metadata.is_malicious


class IngestionResult(BaseModel):
    document_id: str
    status: Literal["indexed", "duplicate", "rejected", "quarantined"]
    chunks_created: int = 0
    content_hash: str
    security_flags: list[str] = Field(default_factory=list)
    reason: str | None = None
