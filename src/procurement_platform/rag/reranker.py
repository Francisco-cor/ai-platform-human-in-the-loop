"""Reranker cross-encoder F4-4 — reordena top 20→top 5 y verifica citas.

Fallback heurístico si sentence-transformers no disponible o sin clave.
"""

from __future__ import annotations

import re
from typing import Any

from procurement_platform.rag.models import RetrievalResult


_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _token_overlap_score(query: str, text: str) -> float:
    q_tokens = set(t.lower() for t in _TOKEN_RE.findall(query))
    d_tokens = set(t.lower() for t in _TOKEN_RE.findall(text))
    if not q_tokens:
        return 0.0
    # recall ponderado + longitud normalizada
    overlap = len(q_tokens & d_tokens) / len(q_tokens)
    # bonus por coincidencia exacta de frase (bigram)
    q_bigrams = (
        set(zip(sorted(q_tokens), sorted(list(q_tokens)[1:]), strict=False))
        if len(q_tokens) > 1
        else set()
    )
    d_bigrams = (
        set(zip(sorted(d_tokens), sorted(list(d_tokens)[1:]), strict=False))
        if len(d_tokens) > 1
        else set()
    )
    bigram_overlap = len(q_bigrams & d_bigrams) / len(q_bigrams) if q_bigrams else 0
    return 0.8 * overlap + 0.2 * bigram_overlap


class CrossEncoderReranker:
    """Re-ranker: cross-encoder real si disponible, fallback heurístico.

    - Si PROCUREMENT_RERANKER_ENABLED=true, retrieval usará este reranker para
      reordenar top_k*4 -> top_k y verificar citas.
    - Intenta cargar sentence-transformers CrossEncoder('cross-encoder/ms-marco-MiniLM-L-6-v2')
      lazy; si falla usa heurística BM25-overlap.
    - Verifica que cita contiene section/page y que el texto contiene evidencia.
    """

    def __init__(self, model_name: str | None = None, use_api: bool = False) -> None:
        self.model_name = model_name or "cross-encoder/ms-marco-MiniLM-L-6-v2"
        self.use_api = use_api
        self._model = None
        self._load_attempted = False

    def _load(self) -> None:
        if self._load_attempted:
            return
        self._load_attempted = True
        if self.use_api:
            return
        try:
            from sentence_transformers import CrossEncoder  # type: ignore

            self._model = CrossEncoder(self.model_name)  # type: ignore
        except Exception:
            self._model = None

    def score(self, query: str, doc_text: str) -> float:
        """Score 0-1 para par query-doc."""
        self._load()
        if self._model is not None:
            try:
                # cross-encoder predict returns logits, normalize via sigmoid approx
                import math

                raw = float(self._model.predict([[query, doc_text]])[0])  # type: ignore
                # sigmoid
                return 1 / (1 + math.exp(-raw))
            except Exception:
                pass
        # fallback heurístico
        return _token_overlap_score(query, doc_text)

    def rerank(
        self, query: str, results: list[RetrievalResult], top_k: int | None = None
    ) -> list[RetrievalResult]:
        if not results:
            return results
        top_k = top_k or len(results)
        # score each
        scored: list[tuple[RetrievalResult, float]] = []
        for r in results:
            s = self.score(query, r.chunk.text)
            # verificar cita: si tiene section/page es +0.05 bonus (verified)
            if r.citation.get("section") and r.citation.get("page") is not None:
                s = min(1.0, s + 0.02)
            # almacenar rerank_score en citation
            r.citation["rerank_score"] = round(s, 4)
            # also add verified flag
            r.citation["citation_verified"] = bool(
                r.citation.get("document_id") and r.citation.get("version")
            )
            scored.append((r, s))
        # ordenar por rerank_score desc, luego score original
        scored.sort(key=lambda x: (x[1], x[0].score), reverse=True)
        return [r for r, _ in scored[:top_k]]

    def verify_citation(self, result: RetrievalResult) -> dict[str, Any]:
        """Verifica que cita tenga campos requeridos y texto contenga referencia."""
        cit = result.citation
        checks = {
            "has_document_id": bool(cit.get("document_id")),
            "has_version": bool(cit.get("version")),
            "has_page": cit.get("page") is not None,
            "has_section": bool(cit.get("section")),
            "has_score": cit.get("score") is not None,
            "has_rerank_score": cit.get("rerank_score") is not None,
        }
        # text contains section?
        section = cit.get("section")
        if section and isinstance(result.chunk.text, str):
            checks["section_in_text"] = section.lower() in result.chunk.text.lower()[:500].lower()
        else:
            checks["section_in_text"] = False
        checks["verified"] = all([checks["has_document_id"], checks["has_version"]])
        return checks


# Singleton
_global_reranker: CrossEncoderReranker | None = None


def get_reranker() -> CrossEncoderReranker:
    global _global_reranker
    if _global_reranker is None:
        _global_reranker = CrossEncoderReranker()
    return _global_reranker


def reset_reranker() -> None:
    global _global_reranker
    _global_reranker = None
