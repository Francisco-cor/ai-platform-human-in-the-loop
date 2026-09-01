"""F4-1 GeminiEmbedder adapter tests."""

from procurement_platform.rag.embeddings import (
    FakeEmbedder,
    GeminiEmbedder,
    get_embedder,
    reset_embedder_cache,
    cosine_similarity,
)


def test_fake_determinism():
    emb = FakeEmbedder(dim=384)
    v1 = emb.embed("Política límite 5000")
    v2 = emb.embed("Política límite 5000")
    assert v1 == v2
    assert len(v1) == 384
    assert abs(sum(x * x for x in v1) - 1.0) < 1e-5


def test_fake_cosine_identical():
    emb = FakeEmbedder()
    a = emb.embed("hello world")
    b = emb.embed("hello world")
    import pytest

    assert cosine_similarity(a, b) == pytest.approx(1.0, abs=1e-6)


def test_gemini_fallback_without_key():
    # without GEMINI_API_KEY, should fallback to deterministic fake
    ge = GeminiEmbedder(api_key=None, model="models/text-embedding-004", dim=384)
    v1 = ge.embed("test text for fallback")
    v2 = ge.embed("test text for fallback")
    assert v1 == v2
    assert len(v1) == 384
    # fallback embeddings should equal FakeEmbedder for same text? not necessarily but deterministic
    fake = FakeEmbedder(dim=384)
    # fallback uses internal FakeEmbedder with same hash logic, should match
    assert v1 == fake.embed("test text for fallback")


def test_get_embedder_factory_fake():
    reset_embedder_cache()
    import os

    os.environ["PROCUREMENT_EMBEDDER"] = "fake"
    from procurement_platform.config.settings import reset_settings_cache

    reset_settings_cache()
    emb = get_embedder()
    assert isinstance(emb, FakeEmbedder)
    reset_embedder_cache()
    reset_settings_cache()


def test_get_embedder_factory_gemini():
    reset_embedder_cache()
    import os

    os.environ["PROCUREMENT_EMBEDDER"] = "gemini"
    from procurement_platform.config.settings import reset_settings_cache

    reset_settings_cache()
    emb = get_embedder()
    assert isinstance(emb, GeminiEmbedder)
    # cleanup
    os.environ["PROCUREMENT_EMBEDDER"] = "fake"
    reset_embedder_cache()
    reset_settings_cache()
