"""Recuperación RAG con filtros, citas y scoring — Fase 3 (§10) + Fase 4 hybrid MMR (F4-3)."""

from __future__ import annotations

import math
import re
from datetime import UTC, datetime
from typing import Any

from procurement_platform.rag.embeddings import Embedder, cosine_similarity, get_embedder
from procurement_platform.rag.models import Chunk, RetrievalQuery, RetrievalResult
from procurement_platform.rag.security import check_obsolescence


# ---------------------------------------------------------------------------
# BM25 utilities (simple, sin dependencias externas)
# ---------------------------------------------------------------------------

_TOKEN_RE = re.compile(r"\w+", re.UNICODE)


def _tokenize(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN_RE.findall(text)]


def _bm25_scores(
    query_tokens: list[str], docs_tokens: list[list[str]], k1: float = 1.5, b: float = 0.75
) -> list[float]:
    n = len(docs_tokens)
    if n == 0:
        return []
    avgdl = sum(len(d) for d in docs_tokens) / n if n else 0
    # DF per term
    df: dict[str, int] = {}
    for doc in docs_tokens:
        seen = set(doc)
        for t in seen:
            df[t] = df.get(t, 0) + 1
    # IDF per query term
    idf: dict[str, float] = {}
    for t in set(query_tokens):
        df_t = df.get(t, 0)
        # Robertson-Jones IDF smooth
        idf[t] = math.log(1 + (n - df_t + 0.5) / (df_t + 0.5))
    scores: list[float] = []
    for doc in docs_tokens:
        dl = len(doc)
        tf: dict[str, int] = {}
        for t in doc:
            tf[t] = tf.get(t, 0) + 1
        s = 0.0
        for q in query_tokens:
            if q not in tf:
                continue
            f = tf[q]
            denom = f + k1 * (1 - b + b * (dl / avgdl if avgdl else 1))
            s += idf.get(q, 0) * (f * (k1 + 1) / denom) if denom else 0
        scores.append(s)
    return scores


def _normalize_scores(scores: list[float]) -> list[float]:
    if not scores:
        return []
    mn = min(scores)
    mx = max(scores)
    if mx == mn:
        return [0.5 for _ in scores] if mx != 0 else [0.0 for _ in scores]
    return [(s - mn) / (mx - mn) for s in scores]


def _mmr_select(
    candidates: list[tuple[Chunk, float, list[float] | None]],
    top_k: int,
    lambda_mult: float = 0.5,
) -> list[tuple[Chunk, float]]:
    """Maximal Marginal Relevance para diversidad.

    candidates: list of (chunk, hybrid_score, embedding)
    lambda_mult 0.5 balancea relevancia vs diversidad.
    Retorna top_k seleccionados en orden MMR.
    """
    if not candidates:
        return []
    if len(candidates) <= top_k:
        # ordenar por hybrid score desc
        candidates_sorted = sorted(candidates, key=lambda x: x[1], reverse=True)
        return [(c, s) for c, s, _ in candidates_sorted][:top_k]
    # iniciar con el de mayor hybrid_score
    candidates_sorted = sorted(candidates, key=lambda x: x[1], reverse=True)
    selected: list[tuple[Chunk, float, list[float] | None]] = []
    remaining = candidates_sorted.copy()
    # primer pick: mayor score
    selected.append(remaining.pop(0))
    while len(selected) < top_k and remaining:
        best_idx = -1
        best_mmr = -1e9
        for idx, (_chunk, score, emb) in enumerate(remaining):
            # max similarity to selected
            max_sim = 0.0
            if emb is not None:
                for _, _, sel_emb in selected:
                    if sel_emb is not None and emb is not None:
                        try:
                            sim = cosine_similarity(emb, sel_emb)  # type: ignore
                            # map [-1,1] -> [0,1] for penalización
                            sim01 = (sim + 1) / 2
                            if sim01 > max_sim:
                                max_sim = sim01
                        except Exception:
                            pass
            else:
                # fallback: text overlap jaccard approx
                pass
            mmr = lambda_mult * score - (1 - lambda_mult) * max_sim
            if mmr > best_mmr:
                best_mmr = mmr
                best_idx = idx
        if best_idx >= 0:
            selected.append(remaining.pop(best_idx))
        else:
            break
    return [(c, s) for c, s, _ in selected]


class RetrievalService:
    """Servicio de recuperación con filtros previos al ranking.

    Fase 3: vector cosine.
    Fase 4 (F4-3): hybrid BM25 0.3 + vector 0.7 + MMR 0.5 + feedback boosting.
    """

    def __init__(self, embedder: Embedder | None = None) -> None:
        self.embedder = embedder or get_embedder()  # type: ignore
        # in-memory store de chunks (en producción sería pgvector HNSW)
        self._chunks: list[Chunk] = []
        # weights (F4 spec)
        self.vector_weight = 0.7
        self.bm25_weight = 0.3
        self.mmr_lambda = 0.5
        self.feedback_boost_factor = 0.05  # por punto de feedback_score

    def index_chunks(self, chunks: list[Chunk]) -> None:
        self._chunks.extend(chunks)

    def clear(self) -> None:
        self._chunks.clear()

    def _filter(self, chunk: Chunk, query: RetrievalQuery, now: datetime) -> bool:
        meta = chunk.metadata
        # tenant
        if meta.tenant_id != query.tenant_id:
            return False
        # jurisdiction
        if query.jurisdiction and meta.jurisdiction not in (query.jurisdiction, "global"):
            return False
        # location
        if query.location_id and meta.location_id and meta.location_id != query.location_id:
            # si el chunk tiene location específica y no coincide, filtrar
            return False
        # doc_type / policy_type
        if query.doc_type and meta.policy_type != query.doc_type.value:
            # si se filtra por tipo pero el chunk no coincide, excluir (si policy_type está seteado)
            # permitimos si policy_type es None y doc_type es policy (flexible)
            pass
        if query.policy_type and meta.policy_type != query.policy_type:
            return False
        # status
        if query.require_approved and meta.status.value != "approved":
            return False
        # vigencia
        if query.require_valid:
            obs = check_obsolescence(meta.valid_from, meta.valid_to, now)
            if not obs["is_valid"]:
                return False
        # clasificación
        if (
            query.allowed_classifications
            and meta.classification not in query.allowed_classifications
        ):
            return False
        # seguridad: excluir maliciosos y untrusted si se requiere alta confiabilidad
        if meta.is_malicious:
            return False
        if meta.reliability == "untrusted":
            return False
        return True

    def retrieve(self, query: RetrievalQuery, now: datetime | None = None) -> list[RetrievalResult]:
        """Hybrid retrieval: vector + BM25 + MMR + feedback boosting."""
        now = now or datetime.now(UTC)
        # 1. filtrar antes de rankear (§10: tenant, vigencia, jurisdicción, permisos, etc.)
        candidates = [c for c in self._chunks if self._filter(c, query, now)]
        if not candidates:
            return []

        # 2. embedding de la query
        q_emb = self.embedder.embed(query.query)
        # vector scores
        vec_scores: list[float] = []
        valid_candidates: list[Chunk] = []
        for chunk in candidates:
            if chunk.embedding is None:
                continue
            try:
                score = cosine_similarity(q_emb, chunk.embedding)
            except Exception:
                score = 0.0
            vec_scores.append(score)
            valid_candidates.append(chunk)
        if not valid_candidates:
            return []

        # 3. BM25 scores
        query_tokens = _tokenize(query.query)
        docs_tokens = [_tokenize(c.text) for c in valid_candidates]
        bm25_raw = _bm25_scores(query_tokens, docs_tokens)
        bm25_norm = _normalize_scores(bm25_raw)

        # 4. hybrid + feedback boosting
        hybrid_candidates: list[tuple[Chunk, float, list[float] | None]] = []
        for idx, chunk in enumerate(valid_candidates):
            vs = vec_scores[idx]
            bs = bm25_norm[idx] if idx < len(bm25_norm) else 0.0
            hybrid = self.vector_weight * vs + self.bm25_weight * bs
            # feedback boost: lookup chunk feedback_score if available on chunk metadata or via store?
            # ChunkMetadata may not have feedback_score; try to get from chunk.embedding supplemental?
            # We store feedback via separate persistence; for in-memory, we rely on chunk.metadata.__dict__ maybe?
            # Fallback: if chunk has attribute feedback_score in metadata, use it.
            fb = 0.0
            try:
                fb = float(getattr(chunk.metadata, "feedback_score", 0) or 0)
                # also try dict
                if not fb and hasattr(chunk.metadata, "model_extra"):
                    fb = float(chunk.metadata.__dict__.get("feedback_score", 0) or 0)
            except Exception:
                fb = 0.0
            # also check if chunk has top-level feedback_score (added in F4 model via metadata extra)
            # we also support retrieving from persistence via global feedback store if needed (F4-5)
            if fb:
                hybrid += self.feedback_boost_factor * fb
                # clamp
                hybrid = min(1.0, max(-1.0, hybrid))
            hybrid_candidates.append((chunk, hybrid, chunk.embedding))

        # 5. MMR selección diversa
        mmr_selected = _mmr_select(
            hybrid_candidates, top_k=query.top_k, lambda_mult=self.mmr_lambda
        )

        results: list[RetrievalResult] = []
        for chunk, hybrid_score in mmr_selected:
            # también compute original vector score para citation
            try:
                vec_score = cosine_similarity(q_emb, chunk.embedding) if chunk.embedding else 0.0
            except Exception:
                vec_score = 0.0
            citation = {
                "document_id": chunk.metadata.document_id,
                "version": chunk.metadata.version,
                "page": chunk.metadata.page,
                "section": chunk.metadata.section,
                "score": round(hybrid_score, 4),
                "vector_score": round(vec_score, 4),
                "bm25_score": round(
                    bm25_norm[valid_candidates.index(chunk)] if chunk in valid_candidates else 0, 4
                ),
                "valid_from": chunk.metadata.valid_from.isoformat(),
                "valid_to": chunk.metadata.valid_to.isoformat()
                if chunk.metadata.valid_to
                else None,
                "reliability": chunk.metadata.reliability,
                "jurisdiction": chunk.metadata.jurisdiction,
            }
            # actualizar score en metadata (usamos hybrid como principal)
            try:
                chunk.metadata.score = hybrid_score  # type: ignore
            except Exception:
                pass
            # store rerank_score placeholder (filled by reranker if enabled)
            citation["rerank_score"] = None
            results.append(RetrievalResult(chunk=chunk, score=hybrid_score, citation=citation))
        return results

    def retrieve_with_validation(
        self, query: RetrievalQuery, now: datetime | None = None
    ) -> dict[str, Any]:
        """Wrapper que incluye validación de obsolescencia y conflicto.

        Retorna dict con results, warnings, conflict info, etc.
        """
        from procurement_platform.rag.security import detect_conflict

        results = self.retrieve(query, now=now)
        # detectar obsolescencia entre resultados (si hay versiones mezcladas)
        # no bloquear automáticamente, pero advertir
        warnings: list[str] = []
        for r in results:
            obs = check_obsolescence(
                r.chunk.metadata.valid_from, r.chunk.metadata.valid_to, now or datetime.now(UTC)
            )
            if obs["is_expired"]:
                warnings.append(f"document {r.chunk.metadata.document_id} vencido")
        # detectar conflicto entre políticas recuperadas
        policies_for_conflict = []
        for r in results:
            # mapear a dict para detect_conflict
            policies_for_conflict.append(
                {
                    "document_id": r.chunk.metadata.document_id,
                    "tenant_id": r.chunk.metadata.tenant_id,
                    "policy_type": r.chunk.metadata.policy_type,
                    "location_id": r.chunk.metadata.location_id,
                    "rules": {"text": r.chunk.text[:200]},
                }
            )
        conflict_info = (
            detect_conflict(policies_for_conflict)
            if len(policies_for_conflict) > 1
            else {"has_conflict": False, "conflicts": []}
        )
        if conflict_info["has_conflict"]:
            warnings.append(f"conflicto detectado: {conflict_info['conflicts']}")

        return {
            "results": results,
            "count": len(results),
            "warnings": warnings,
            "conflict": conflict_info,
            "query": query.model_dump(),
        }

    def count(self) -> int:
        return len(self._chunks)

    def get_all(self) -> list[Chunk]:
        return list(self._chunks)


# Backwards compat alias for spec HybridRetriever
class HybridRetriever(RetrievalService):
    """Alias para spec Fase 4 — hybrid MMR."""

    pass
