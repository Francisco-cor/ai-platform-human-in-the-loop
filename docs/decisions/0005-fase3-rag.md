# ADR 0005 — RAG seguro Fase 3

**Fecha:** 2026-08-20  
**Estado:** Aceptada  
**Fase:** 3

## Contexto

RAG debe recuperar políticas relevantes con trazabilidad y demostrar que contenido no confiable no controla al agente (§10). Se requiere pipeline GCS → extracción → clasificación → chunks → embeddings → pgvector, con filtros tenant/vigencia/jurisdicción y defensa contra injection, obsolescencia y conflictos.

## Decisión

Implementar RAG con defensa multicapa y embeddings fake deterministas:

**Modelos** (`rag/models.py`): `DocumentMetadata`/`Document` con tenant, tipo, clasificación, jurisdicción, version, `valid_from/to`, status, allowed_tenants/roles, `content_hash`, `security_flags`, `is_malicious`; `ChunkMetadata` con misma metadata + `policy_type`, `reliability` (high/medium/low/untrusted), `is_malicious`; `RetrievalQuery` con filtros y `top_k`; `IngestionResult` con estado `indexed/duplicate/rejected/quarantined`.

**Embeddings** (`rag/embeddings.py`): `FakeEmbedder` 384 dims, hash SHA256 determinista + normalización L2, `cosine_similarity`. En producción se reemplaza por Gemini/otro detrás de protocolo `Embedder` sin cambiar contratos. Ventaja: determinismo para tests, no requiere modelo real en CI.

**Seguridad** (`rag/security.py`): `INJECTION_PATTERNS` (regex para `ignore previous instructions`, `approve supplier`, español `ignora instrucciones`, etc.), `detect_prompt_injection()` con severidad, `classify_content()` separa normativo vs no confiable, `check_obsolescence()` (`valid_to < now`), `detect_conflict()` agrupa por `(tenant, policy_type, location)` y compara valores, `should_block_execution()` decide bloqueo si `is_malicious` o `has_conflict` o `reliability==untrusted`. Cada capa es independiente: clasificación, separación de mensajes, allowlist, policy engine y pruebas adversariales.

**Ingesta** (`rag/ingestion.py`): `IngestionPipeline` → 1) hash + dedup, 2) scan malware stub (extensiones), 3) extracción preservando páginas/secciones, 4) detección injection sobre doc completo, 5) si malicioso → `quarantined` (conserva evidencia, no indexa), 6) fragmentación (`chunk_size 500, overlap 50`) con reclasificación por chunk, 7) embeddings, 8) registro `content_hash` y `security_flags`. Cada chunk lleva metadata de tenant, versión, vigencia y permisos (Fase 3 §10).

**Retrieval** (`rag/retrieval.py`): `RetrievalService` filtra **antes** de rankear por tenant, jurisdicción, location, `policy_type`, `require_approved`, `require_valid` (obsolescencia), clasificación y `is_malicious`/`untrusted`. Luego rankea por cosine y retorna top_k con `citation` (`document_id`, `version`, `page/section`, `score`, `valid_from/to`, `reliability`, `jurisdiction`). `retrieve_with_validation()` añade `warnings` y `conflict` via `detect_conflict`.

**Servicio** (`rag/service.py`): `RagService` orquesta ingesta+retrieval+auditoría, persiste a `documents`/`document_chunks` (JSON para SQLite, `vector(384)` para pgvector en prod), método `retrieve_for_execution()` retorna `(results, should_block, reason)` y es usado por `workflows/orchestrator.py` en nodo `POLICY_RETRIEVED`.

**Persistencia** (`persistence/models.py`, migration `003_rag_documents`): tablas `documents` y `document_chunks` con índices `tenant_id`, `policy_type`, `content_hash`.

**Integración workflow:** `orchestrator.py` añade `_get_rag_service()` (singleton con seed de políticas por defecto: budget 5000 y supplier allowlist) y `_retrieve_policies_for_execution()`. En `advance_synthetic()` al llegar a `POLICY_RETRIEVED` se recupera via RAG, se audita `rag.retrieval.completed/blocked` y si `should_block` → transición a `BLOCKED` (criterio salida Fase 3: bloquea caso malicioso, no ejecuta acción basada en texto no confiable).

## Consecuencias

- Retrieval trazable con citas internas y etiqueta de confiabilidad; filtros garantizan que política vencida no supera vigente por score.
- Prompt injection multicapa: detección en ingesta (quarantine) + filtrado en retrieval + bloqueo en orchestrator + caso evaluación `malicious_document.json` con `required_events security.prompt_injection_detected`.
- Conflicto: dos políticas mismo tenant/location con valores distintos → `detect_conflict` → BLOCKED, no resolución textual.
- Evaluación: corpus etiquetado con `FakeEmbedder` determinista permite medir precision/recall sin modelo real; `test_retrieval_precision_recall` verifica determinismo y recall 1.0 con top_k 5.
- NEXT: Fase 4 conectará Gemini via adapter manteniendo misma interfaz `Embedder` y `RagService`.

## Alternativas descartadas

- Usar modelo real de embeddings en CI: no determinista y requiere credenciales; se usa fake con misma interfaz.
- pgvector obligatorio en tests: se usa JSON + ranking en Python para CI, con migración lista para vector en prod.
- Una sola capa de defensa (prompt): se adoptan 5 capas como exige §10.
