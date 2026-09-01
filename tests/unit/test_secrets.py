"""Secret provider tests — F3-3."""

import os

from procurement_platform.config.secrets import SecretProvider


def test_secret_from_env(monkeypatch):
    monkeypatch.setenv("TEST_SECRET_F3", "s3cr3t")
    prov = SecretProvider(use_gcp=False)
    assert prov.get("TEST_SECRET_F3") == "s3cr3t"
    prov.clear()


def test_secret_missing_default(monkeypatch):
    monkeypatch.delenv("MISSING_SECRET_XYZ", raising=False)
    prov = SecretProvider(use_gcp=False)
    assert prov.get("MISSING_SECRET_XYZ", default="def") == "def"


def test_secret_cache(monkeypatch):
    monkeypatch.setenv("CACHED_SECRET", "val1")
    prov = SecretProvider(use_gcp=False)
    assert prov.get("CACHED_SECRET") == "val1"
    # change env but cache should still return old
    monkeypatch.setenv("CACHED_SECRET", "val2")
    assert prov.get("CACHED_SECRET") == "val1"
    prov.clear()
    assert prov.get("CACHED_SECRET") == "val2"
