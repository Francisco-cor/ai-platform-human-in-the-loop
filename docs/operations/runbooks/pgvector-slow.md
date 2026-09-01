# Runbook — pgvector / RAG lento (F4-5)

**Síntoma:** `GET /v1/rag/search` p95 >200ms o `rag_retrieval_latency_seconds` >0.5s alerta `RagLatencyHigh`.

## Diagnóstico

1. **Métricas:** `curl /metrics | grep rag_retrieval` → `rag_retrieval_latency_seconds_bucket`.
2. **Logs:** `rag.retrieval.completed` con `duration_ms` en `audit_events` → `GET .../events?format=trace` y filtra `event_type=rag.retrieval.*`.
3. **Explain:** `GET /v1/rag/search?query=presupuesto&tenant_id=tenant_demo` → verifica `count`, `conflict`, `reranked`.
4. **DB:** `EXPLAIN ANALYZE SELECT * FROM document_chunks WHERE tenant_id='tenant_demo' LIMIT 5;` y `SELECT * FROM pg_indexes WHERE tablename='document_chunks';` → debe existir `ix_chunks_embedding_hnsw` (pgvector HNSW).
5. **Grafana:** panel `RAG p95` — si >0.5s por 3m, alerta.

## Causas

- `pgvector` sin `HNSW` (índice no creado, caída a `JSON` scan) → `migrations/versions/005` no aplicada en staging (solo `sqlite` local).
- `PROCUREMENT_RERANKER_ENABLED=true` + `sentence-transformers` 700MB carga lenta → desactiva para prueba `=false`.
- `BM25` con 100k chunks sin cache → `rag/retrieval.py` MMR `λ0.5` coste O(n*k).

## Acciones

- **Reindex:** `make eval-rag` debe <30s para 100 docs; si >30s, `docker compose` con `pgvector:pg16` y `hnsw` tunning `m=16 ef_construction=64`.
- **Toggle:** `PROCUREMENT_RERANKER_ENABLED=false` para medir sin re-ranker.
- **Escalado:** `EXPLAIN` y `CREATE INDEX CONCURRENTLY` si faltante.

**SLO:** RAG p95 <200ms local, <500ms staging (alerta 0.5s).

