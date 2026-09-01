"""F4-2 — pgvector HNSW vector(384) + feedback columns (RAG 2.0 production)

Revision ID: 005_pgvector_hnsw
Revises: 004_outbox
Create Date: 2026-09-01
"""

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

revision: str = "005_pgvector_hnsw"
down_revision: Union[str, None] = "004_outbox"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name if bind is not None else "sqlite"

    if dialect == "postgresql":
        # pgvector extension
        try:
            op.execute(sa.text("CREATE EXTENSION IF NOT EXISTS vector"))
        except Exception:
            pass
        # embedding_vec vector(384)
        try:
            op.execute(sa.text("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS embedding_vec vector(384)"))
        except Exception:
            try:
                op.add_column("document_chunks", sa.Column("embedding_vec", sa.JSON(), nullable=True))
            except Exception:
                pass
        # feedback columns
        try:
            op.execute(sa.text("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS feedback_score DOUBLE PRECISION DEFAULT 0"))
        except Exception:
            try:
                op.add_column("document_chunks", sa.Column("feedback_score", sa.Float(), nullable=False, server_default="0"))
            except Exception:
                pass
        try:
            op.execute(sa.text("ALTER TABLE document_chunks ADD COLUMN IF NOT EXISTS feedback_count INTEGER DEFAULT 0"))
        except Exception:
            try:
                op.add_column("document_chunks", sa.Column("feedback_count", sa.Integer(), nullable=False, server_default="0"))
            except Exception:
                pass
        # gcs_uri on documents
        try:
            op.execute(sa.text("ALTER TABLE documents ADD COLUMN IF NOT EXISTS gcs_uri VARCHAR(512)"))
        except Exception:
            try:
                op.add_column("documents", sa.Column("gcs_uri", sa.String(length=512), nullable=True))
            except Exception:
                pass
        # HNSW index (pgvector)
        try:
            op.execute(sa.text("CREATE INDEX IF NOT EXISTS ix_chunks_embedding_hnsw ON document_chunks USING hnsw (embedding_vec vector_cosine_ops)"))
        except Exception:
            pass
    else:
        # SQLite / other — add via alembic batch (idempotent with try)
        try:
            op.add_column("document_chunks", sa.Column("embedding_vec", sa.JSON(), nullable=True))
        except Exception:
            pass
        try:
            op.add_column("document_chunks", sa.Column("feedback_score", sa.Float(), nullable=False, server_default="0"))
        except Exception:
            pass
        try:
            op.add_column("document_chunks", sa.Column("feedback_count", sa.Integer(), nullable=False, server_default="0"))
        except Exception:
            pass
        try:
            op.add_column("documents", sa.Column("gcs_uri", sa.String(length=512), nullable=True))
        except Exception:
            pass


def downgrade() -> None:
    bind = op.get_bind()
    dialect = bind.dialect.name if bind is not None else "sqlite"
    if dialect == "postgresql":
        try:
            op.execute(sa.text("DROP INDEX IF EXISTS ix_chunks_embedding_hnsw"))
        except Exception:
            pass
        try:
            op.execute(sa.text("ALTER TABLE document_chunks DROP COLUMN IF EXISTS embedding_vec"))
        except Exception:
            try:
                op.drop_column("document_chunks", "embedding_vec")
            except Exception:
                pass
        try:
            op.execute(sa.text("ALTER TABLE document_chunks DROP COLUMN IF EXISTS feedback_score"))
        except Exception:
            try:
                op.drop_column("document_chunks", "feedback_score")
            except Exception:
                pass
        try:
            op.execute(sa.text("ALTER TABLE document_chunks DROP COLUMN IF EXISTS feedback_count"))
        except Exception:
            try:
                op.drop_column("document_chunks", "feedback_count")
            except Exception:
                pass
        try:
            op.execute(sa.text("ALTER TABLE documents DROP COLUMN IF EXISTS gcs_uri"))
        except Exception:
            try:
                op.drop_column("documents", "gcs_uri")
            except Exception:
                pass
    else:
        try:
            op.drop_column("document_chunks", "embedding_vec")
        except Exception:
            pass
        try:
            op.drop_column("document_chunks", "feedback_score")
        except Exception:
            pass
        try:
            op.drop_column("document_chunks", "feedback_count")
        except Exception:
            pass
        try:
            op.drop_column("documents", "gcs_uri")
        except Exception:
            pass
