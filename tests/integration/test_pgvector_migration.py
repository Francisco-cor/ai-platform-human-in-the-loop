"""F4-2 pgvector HNSW migration test — skip if SQLite."""

import pytest
from sqlalchemy import inspect

from procurement_platform.persistence.database import get_engine


def test_pgvector_columns_exist():
    engine = get_engine()
    insp = inspect(engine)
    # document_chunks should have new columns
    cols = {c["name"] for c in insp.get_columns("document_chunks")}
    assert "embedding_vec" in cols, (
        "embedding_vec missing - migration 005 not applied or model not synced"
    )
    assert "feedback_score" in cols
    assert "feedback_count" in cols
    # documents gcs_uri
    cols_doc = {c["name"] for c in insp.get_columns("documents")}
    assert "gcs_uri" in cols_doc


@pytest.mark.skipif(get_engine().dialect.name == "sqlite", reason="pgvector HNSW only on postgres")
def test_pgvector_hnsw_index_pg():
    engine = get_engine()
    insp = inspect(engine)
    indexes = insp.get_indexes("document_chunks")
    names = [idx["name"] for idx in indexes]
    assert any("hnsw" in n.lower() or "embedding" in n.lower() for n in names)
