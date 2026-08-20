"""Embeddings deterministas fake — Fase 3.

Para no depender de modelo real en tests/CI, usamos embeddings fake deterministas
basados en hash del texto. En producción se reemplazaría por proveedor real (Gemini, etc.)
detrás de la misma interfaz.
"""
from __future__ import annotations

import hashlib
import math
import struct
from typing import Protocol


class Embedder(Protocol):
    def embed(self, text: str) -> list[float]:
        ...

    def embed_many(self, texts: list[str]) -> list[list[float]]:
        ...


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


def cosine_similarity(a: list[float], b: list[float]) -> float:
    if len(a) != len(b):
        raise ValueError("dim mismatch")
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    # ya están normalizados, dot es cosine
    # clamp a [-1,1] por errores float
    return max(-1.0, min(1.0, dot))


# Singleton por defecto
_default_embedder: FakeEmbedder | None = None


def get_embedder() -> FakeEmbedder:
    global _default_embedder
    if _default_embedder is None:
        _default_embedder = FakeEmbedder()
    return _default_embedder
