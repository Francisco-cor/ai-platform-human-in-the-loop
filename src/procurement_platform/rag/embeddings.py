"""Embeddings — Fase 3 fake determinista + Fase 4 Gemini adapter (F4-1).

Protocolo Embedder + FakeEmbedder (384 determinista, hash) para CI + GeminiEmbedder real lazy.
Factory get_embedder() por PROCUREMENT_EMBEDDER=fake|gemini. Mantiene determinismo Fake en CI.
"""

from __future__ import annotations

import hashlib
import math
import os
import struct
from typing import Protocol, runtime_checkable


@runtime_checkable
class Embedder(Protocol):
    dim: int
    model_name: str

    def embed(self, text: str) -> list[float]: ...

    def embed_many(self, texts: list[str]) -> list[list[float]]: ...


class FakeEmbedder:
    """Embedder fake determinista — 384 dims, normalizado L2.

    Usa SHA256 del texto para generar vector pseudo-aleatorio pero determinista.
    """

    def __init__(self, dim: int = 384, model_name: str = "fake-384") -> None:
        self.dim = dim
        self.model_name = model_name

    def embed(self, text: str) -> list[float]:
        # generar dim floats a partir de hash
        h = hashlib.sha256(text.encode("utf-8")).digest()
        # expandir hash repitiendo
        vec: list[float] = []
        counter = 0
        while len(vec) < self.dim:
            # hash con counter para variar
            data = hashlib.sha256(h + counter.to_bytes(4, "little")).digest()
            # cada 4 bytes -> float en [-1,1]
            for i in range(0, len(data), 4):
                if len(vec) >= self.dim:
                    break
                # unpack as unsigned int -> float
                val = struct.unpack("<I", data[i : i + 4])[0]
                # map to [-1, 1]
                f = (val / 0xFFFFFFFF) * 2 - 1
                vec.append(f)
            counter += 1
        # L2 normalize
        norm = math.sqrt(sum(x * x for x in vec))
        if norm > 0:
            vec = [x / norm for x in vec]
        return vec

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class GeminiEmbedder:
    """Adapter para Gemini text-embedding-004 (lazy, sin dependencia dura).

    - Si GEMINI_API_KEY está configurada y google-generativeai disponible, llama al API
      con output_dimensionality=dim para producir vector(384) compatible con pgvector HNSW.
    - Si no, hace fallback determinista a FakeEmbedder (hash) para no romper CI/local sin clave.
    - Mantiene dim y model_name para compatibilidad con retrieval y persistencia.
    """

    def __init__(
        self,
        api_key: str | None = None,
        model: str = "models/text-embedding-004",
        dim: int = 384,
        task_type: str = "retrieval_document",
    ) -> None:
        self.dim = dim
        self.model_name = model
        self.task_type = task_type
        # api_key: explicit or env GEMINI_API_KEY
        self.api_key = api_key or os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY")
        self._fallback = FakeEmbedder(dim=dim, model_name=f"{model}:fallback")
        self._genai = None
        self._use_api = False
        if self.api_key:
            try:
                import google.generativeai as genai  # type: ignore

                genai.configure(api_key=self.api_key)  # type: ignore
                self._genai = genai
                self._use_api = True
            except Exception:
                self._genai = None
                self._use_api = False

    def _embed_via_api(self, text: str) -> list[float] | None:
        if not self._use_api or self._genai is None:
            return None
        try:
            # google-generativeai >=0.5: genai.embed_content(model, content, task_type, output_dimensionality)
            resp = self._genai.embed_content(  # type: ignore
                model=self.model_name,
                content=text,
                task_type=self.task_type,
                output_dimensionality=self.dim,  # type: ignore
            )
            emb = (
                resp.get("embedding")
                if isinstance(resp, dict)
                else getattr(resp, "embedding", None)
            )
            if emb is None and isinstance(resp, dict):
                # some versions return {"embedding": {"values": [...]}}
                emb_obj = resp.get("embedding", {})
                if isinstance(emb_obj, dict):
                    emb = emb_obj.get("values")
            if emb is None:
                return None
            # ensure list[float] length dim: truncate/pad, then L2 normalize for consistency con cosine
            vec = list(emb)[: self.dim]
            if len(vec) < self.dim:
                vec = vec + [0.0] * (self.dim - len(vec))
            # L2 normalize
            import math as _math

            norm = _math.sqrt(sum(x * x for x in vec))
            if norm > 0:
                vec = [x / norm for x in vec]
            return vec
        except Exception:
            return None

    def embed(self, text: str) -> list[float]:
        vec = self._embed_via_api(text)
        if vec is not None:
            return vec
        # fallback determinista (no requiere red, mantiene CI verde)
        return self._fallback.embed(text)

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        # batch via API if available (try embed_content for batch or loop)
        if self._use_api and self._genai is not None:
            # intentar batch; si falla, loop individual con fallback
            out: list[list[float]] = []
            for t in texts:
                v = self._embed_via_api(t)
                out.append(v if v is not None else self._fallback.embed(t))
            return out
        return [self.embed(t) for t in texts]

    @property
    def is_fallback(self) -> bool:
        return not self._use_api


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("dim mismatch")
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    # ya están normalizados, dot es cosine
    # clamp a [-1,1] por errores float
    return max(-1.0, min(1.0, dot))


# Singleton factory per PROCUREMENT_EMBEDDER
_default_embedder: Embedder | None = None
_default_kind: str | None = None


def get_embedder(kind: str | None = None) -> Embedder:
    """Factory por PROCUREMENT_EMBEDDER=fake|gemini (default fake).

    - Lee env Settings.embedder si no se pasa kind.
    - Cachea singleton por kind; usar reset_embedder_cache() en tests.
    """
    global _default_embedder, _default_kind
    if kind is None:
        try:
            from procurement_platform.config.settings import get_settings

            kind = get_settings().embedder
        except Exception:
            kind = os.getenv("PROCUREMENT_EMBEDDER", "fake")
    kind = (kind or "fake").lower()
    if _default_embedder is not None and _default_kind == kind:
        return _default_embedder
    if kind == "gemini":
        try:
            from procurement_platform.config.settings import get_settings

            s = get_settings()
            model = getattr(s, "gemini_embed_model", "models/text-embedding-004")
        except Exception:
            model = os.getenv("GEMINI_EMBED_MODEL", "models/text-embedding-004")
        _default_embedder = GeminiEmbedder(model=model, dim=384)  # type: ignore[assignment]
    else:
        _default_embedder = FakeEmbedder()  # type: ignore[assignment]
    _default_kind = kind
    return _default_embedder


def reset_embedder_cache() -> None:
    global _default_embedder, _default_kind
    _default_embedder = None
    _default_kind = None
