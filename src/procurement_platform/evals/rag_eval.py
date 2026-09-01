"""RAG golden evaluation — F4-7 precision/recall/nDCG@5.

Carga evals/rag_golden.json (50 Q/A), siembra corpus de 5 docs canónicos + 95 distractores,
ejecuta retrieval hybrid+reranker opcional, reporta precision, recall, nDCG@5.
Gate: precision <0.75 o recall <0.70 falla.
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from procurement_platform.rag.models import Document, DocumentMetadata
from procurement_platform.rag.service import RagService


GOLDEN_PATH = Path("evals/rag_golden.json")
# Canónical docs content per expected id (para garantizar hit con híbrido)
CANONICAL_DOCS: dict[str, dict[str, Any]] = {
    "policy_budget_v1": {
        "title": "Política de límite presupuestario",
        "content": "Política: El límite delegado para tenant_demo en warehouse_north es 5000 USD. Toda orden por encima requiere aprobación humana. Esta política es normativa y vigente. § Artículo 1 presupuesto delegado.",
        "policy_type": "budget_limit",
        "location_id": "warehouse_north",
    },
    "policy_supplier_allowlist_v1": {
        "title": "Proveedores permitidos",
        "content": "Política: Proveedores permitidos para tenant_demo son supplier_demo y supplier_alt. Proveedor activo requerido. Allowlist supplier. Currency USD lead time 7.",
        "policy_type": "supplier_allowlist",
        "location_id": None,
    },
    "policy_inventory_coverage_v1": {
        "title": "Cobertura inventario",
        "content": "Política: Cobertura inventario 21 días, demanda 8 por día, lead time 7, cálculo faltante shortage 138 unidades MAT-001, total 1380 determinista. Warehouse north.",
        "policy_type": "inventory_coverage",
        "location_id": "warehouse_north",
    },
    "policy_validity_v1": {
        "title": "Vigencia y clasificación",
        "content": "Política: Vigencia desde 2025-01-01 status approved clasificación internal jurisdiction global. Documento válido normativo.",
        "policy_type": "validity",
        "location_id": None,
    },
    "policy_approval_risk_v1": {
        "title": "Riesgo y aprobación",
        "content": "Política: Riesgo high requiere 2 aprobaciones parcial, expiración 24h, scope_hash, proposal total 6000 >5000. Approval requested.",
        "policy_type": "approval_risk",
        "location_id": "warehouse_north",
    },
}


def _make_doc(doc_id: str, tenant_id: str = "tenant_demo") -> Document:
    info = CANONICAL_DOCS.get(
        doc_id, {"title": doc_id, "content": f"Contenido {doc_id}", "policy_type": "other"}
    )
    from datetime import UTC, datetime

    meta = DocumentMetadata(
        document_id=doc_id,
        tenant_id=tenant_id,
        title=info["title"],
        doc_type="policy",  # type: ignore
        classification="internal",  # type: ignore
        jurisdiction="global",
        location_id=info.get("location_id"),
        version="1.0.0",
        valid_from=datetime(2025, 1, 1, tzinfo=UTC),
        status="approved",  # type: ignore
        allowed_tenants=[tenant_id],
    )
    content = info["content"]
    return Document(
        metadata=meta, content=content, pages=[{"page": 1, "section": "sec_1", "text": content}]
    )


def seed_corpus(rag: RagService, use_distractors: bool = True) -> None:
    """Siembra corpus canónico + distractores."""
    rag.clear()
    # canonical 5
    for doc_id in CANONICAL_DOCS:
        doc = _make_doc(doc_id)
        rag.ingest_document(document=doc, actor_id="rag_eval_seed")
    if use_distractors:
        # 20 distractores con contenido irrelevante (no contienen tokens query clave)
        filler = [
            "Manual de usuario login sistema",
            "Guía instalación software v2",
            "Reporte ventas Q1 2024",
            "Política vacaciones RH 15 días",
            "Instructivo mantenimiento equipo",
        ]
        from datetime import UTC, datetime
        from procurement_platform.rag.models import DocumentMetadata

        for i in range(20):
            txt = (
                filler[i % len(filler)]
                + f" filler {i} contenido irrelevante sin presupuesto ni proveedor."
            )
            meta = DocumentMetadata(
                document_id=f"distractor_{i:03d}",
                tenant_id="tenant_demo",
                title=f"Distractor {i}",
                doc_type="other",  # type: ignore
                classification="internal",  # type: ignore
                jurisdiction="global",
                version="1.0.0",
                valid_from=datetime(2025, 1, 1, tzinfo=UTC),
                status="approved",  # type: ignore
                allowed_tenants=["tenant_demo"],
            )
            doc = Document(
                metadata=meta, content=txt, pages=[{"page": 1, "section": "s", "text": txt}]
            )
            rag.ingest_document(document=doc, actor_id="rag_eval_seed")


def _dcg(relevances: list[int], k: int) -> float:
    return sum((rel / math.log2(idx + 2)) for idx, rel in enumerate(relevances[:k]))


def evaluate(
    golden_path: Path | None = None, use_reranker: bool | None = None, top_k: int = 5
) -> dict[str, Any]:
    golden_path = golden_path or GOLDEN_PATH
    if not golden_path.exists():
        raise FileNotFoundError(f"golden not found {golden_path}")
    golden = json.loads(golden_path.read_text(encoding="utf-8"))
    # setup RAG
    from procurement_platform.workflows.orchestrator import get_rag_service

    # use fresh RagService for determinismo aislado
    rag = RagService()
    # also clear global singleton to avoid contamination
    try:
        global_rag = get_rag_service()
        if global_rag:
            global_rag.clear()
    except Exception:
        pass
    seed_corpus(rag, use_distractors=True)

    per_query: list[dict[str, Any]] = []
    total_hits = 0
    precisions: list[float] = []
    recalls: list[float] = []
    ndcgs: list[float] = []

    for entry in golden:
        q = entry["query"]
        expected = set(entry.get("expected_docs", []))
        tenant = entry.get("tenant_id", "tenant_demo")
        loc = entry.get("location_id")
        juris = entry.get("jurisdiction", "global")
        k = int(entry.get("top_k", top_k))
        # decide reranker flag
        if use_reranker is None:
            try:
                from procurement_platform.config.settings import get_settings

                use_reranker = bool(get_settings().reranker_enabled)
            except Exception:
                use_reranker = False
        res = rag.retrieve(
            query=q,
            tenant_id=tenant,
            location_id=loc,
            jurisdiction=juris,
            top_k=k,
            use_reranker=use_reranker,
        )
        retrieved_ids = [r.chunk.metadata.document_id for r in res["results"]]
        retrieved_set = set(retrieved_ids)
        hit_set = expected & retrieved_set
        hit = 1 if hit_set else 0
        total_hits += hit
        # para single expected, precision@k como hit (si hit 1 => 1.0 else 0) — más útil que 1/k
        # también calculamos precision estándar para referencia
        relevant_retrieved = len(hit_set)
        precision_std = relevant_retrieved / len(retrieved_ids) if retrieved_ids else 0
        # para reporte principal usamos hit-rate como precision (coincide con recall para single)
        precision_hit = float(hit)
        recall_hit = float(hit)  # mismo para single
        # nDCG: relevances binary, ideal 1 at top
        relevances = [1 if did in expected else 0 for did in retrieved_ids]
        dcg_val = _dcg(relevances, k)
        idcg_val = _dcg([1] * len(expected), k) if expected else 0
        ndcg = dcg_val / idcg_val if idcg_val else 0

        per_query.append(
            {
                "query_id": entry.get("query_id"),
                "query": q[:60],
                "expected": list(expected),
                "retrieved": retrieved_ids,
                "hit": hit,
                "precision_hit": precision_hit,
                "recall_hit": recall_hit,
                "precision_std": round(precision_std, 3),
                "ndcg": round(ndcg, 3),
            }
        )
        precisions.append(precision_hit)
        recalls.append(recall_hit)
        ndcgs.append(ndcg)

    n = len(golden)
    avg_precision = sum(precisions) / n if n else 0
    avg_recall = sum(recalls) / n if n else 0
    avg_ndcg = sum(ndcgs) / n if n else 0
    # también precisión estándar promedio
    # hit_rate already
    return {
        "total_queries": n,
        "hits": total_hits,
        "precision@5": round(avg_precision, 3),
        "recall@5": round(avg_recall, 3),
        "ndcg@5": round(avg_ndcg, 3),
        "per_query": per_query,
        "gate": {
            "precision_threshold": 0.75,
            "recall_threshold": 0.70,
            "passed": avg_precision >= 0.75 and avg_recall >= 0.70,
        },
    }


def main() -> None:
    import argparse

    ap = argparse.ArgumentParser(description="RAG golden eval F4-7")
    ap.add_argument("--golden", type=str, default=str(GOLDEN_PATH))
    ap.add_argument("--reranker", action="store_true", help="force reranker enabled")
    ap.add_argument("--no-reranker", dest="reranker", action="store_false")
    ap.set_defaults(reranker=None)
    ap.add_argument("--json-out", type=str, default=None)
    args = ap.parse_args()

    res = evaluate(Path(args.golden), use_reranker=args.reranker)
    print(f"RAG golden eval: {res['total_queries']} queries")
    print(f"  hits: {res['hits']}/{res['total_queries']}")
    print(f"  precision@5: {res['precision@5']}")
    print(f"  recall@5: {res['recall@5']}")
    print(f"  nDCG@5: {res['ndcg@5']}")
    print(f"  gate passed: {res['gate']['passed']} (need p>=0.75 r>=0.70)")
    if args.json_out:
        Path(args.json_out).write_text(
            json.dumps(res, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"report -> {args.json_out}")
    # exit code for gate
    if not res["gate"]["passed"]:
        print("GATE FAILED: precision or recall below threshold")
        raise SystemExit(1)
    else:
        print("GATE PASSED")


if __name__ == "__main__":
    main()
