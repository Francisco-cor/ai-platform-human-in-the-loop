"""F4-6 GCS ingestor tests."""

import tempfile
import os
from pathlib import Path


def test_gcs_parse_uri():
    from procurement_platform.rag.ingestion import GCSIngestor

    gi = GCSIngestor(use_gcs=False)
    b, blob = gi.parse_gcs_uri("gs://my-bucket/path/to/doc.pdf")
    assert b == "my-bucket"
    assert blob == "path/to/doc.pdf"
    b2, blob2 = gi.parse_gcs_uri("file://local/path.txt")
    assert b2 == ""


def test_gcs_download_file_uri():
    from procurement_platform.rag.ingestion import GCSIngestor

    gi = GCSIngestor(use_gcs=False)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8") as f:
        f.write("Contenido de prueba para GCS file:// con política límite 5000")
        path = f.name
    try:
        uri = f"file://{path}"
        text, pages = gi.download_content(uri)
        assert "Contenido de prueba" in text
        assert pages[0]["section"] == "file_section"
    finally:
        os.unlink(path)


def test_gcs_ingest_fake_gs(db_session):
    from procurement_platform.rag.ingestion import GCSIngestor

    gi = GCSIngestor(use_gcs=False)
    status, chunks = gi.ingest_from_gcs(
        gcs_uri="gs://bucket/tenant/fake_doc.pdf",
        metadata_kwargs={"tenant_id": "tenant_demo", "title": "Fake GCS", "version": "1.0.0"},
        db=db_session,
    )
    assert status == "indexed"
    assert len(chunks) == 1
    assert chunks[0].metadata.document_id == "bucket_tenant_fake_doc.pdf"


def test_gcs_version_history(db_session):
    from procurement_platform.rag.ingestion import GCSIngestor, IngestionPipeline

    gi = GCSIngestor(use_gcs=False)
    pipe = IngestionPipeline()
    # first version
    s1, _ = gi.ingest_from_gcs(
        gcs_uri="gs://bucket/tenant/versioned.pdf",
        metadata_kwargs={"tenant_id": "tenant_demo", "title": "Versioned", "version": "1.0.0"},
        db=db_session,
        pipeline=pipe,
    )
    assert s1 == "indexed"
    # second version same uri same content but new version with allow_reindex should also index
    s2, chunks2 = gi.ingest_from_gcs(
        gcs_uri="gs://bucket/tenant/versioned.pdf",
        metadata_kwargs={"tenant_id": "tenant_demo", "title": "Versioned", "version": "2.0.0"},
        db=db_session,
        pipeline=pipe,
        allow_reindex=True,
    )
    assert s2 == "indexed"
    assert chunks2[0].metadata.version == "2.0.0"
    # without allow_reindex, same content same version should be duplicate
    s3, chunks3 = gi.ingest_from_gcs(
        gcs_uri="gs://bucket/tenant/versioned.pdf",
        metadata_kwargs={"tenant_id": "tenant_demo", "title": "Versioned", "version": "2.0.0"},
        db=db_session,
        pipeline=pipe,
    )
    assert s3 == "duplicate"
    assert len(chunks3) == 0


def test_gcs_signed_url():
    from procurement_platform.rag.ingestion import GCSIngestor

    gi = GCSIngestor(use_gcs=False)
    url = gi.generate_signed_url("gs://bucket/path/doc.pdf", expiration_minutes=10)
    assert url == "gs://bucket/path/doc.pdf"  # fallback without client

    url2 = gi.generate_signed_url("https://example.com/doc.pdf")
    assert url2 == "https://example.com/doc.pdf"
