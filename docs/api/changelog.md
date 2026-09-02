# API Changelog — Procurement Platform

Todas las cambios notables de la API se documentan aquí. Formato basado en Keep a Changelog y versionado semántico.

## [0.1.0] - 2026-09-02 — Fase 8 API Platform

### Added
- `POST /v1/procurement/executions` — crear ejecución (Idempotency-Key, RateLimit)
- `GET /v1/procurement/executions` — lista paginada con `limit`, `cursor`, `tenant_id`, `state`, orden estable `created_at asc, execution_id asc`, `total_count`, `has_more`
- `GET /v1/procurement/executions/{execution_id}` — detalle con `proposal`, `approval_request`, `trace_id`
- `GET /v1/procurement/executions/{execution_id}/events` — paginación cursor `limit/cursor`, `format=trace` timeline con `trace_id/span_id/model_metadata`
- `POST /v1/procurement/executions/{execution_id}/resume` — reanudación durable
- `GET /v1/approvals` — inbox list con `tenant_id/state/limit/cursor`, total `has_more`
- `GET /v1/approvals/{approval_id}` — snapshot inmutable + `proposal_current` + `ScopeDiff` data
- `POST /v1/approvals/{approval_id}/decision` — approved/rejected/needs_changes con `scope_hash` validation, doble aprobación, `Idempotency-Key`
- `POST /v1/approvals/bulk/decision` — bulk `approval_ids[]` + `decision` (RBAC admin/approver)
- `GET /v1/approvals/export?tenant=&state=` — CSV export con `Content-Disposition`
- `POST /v1/approvals/delegation` / `GET /v1/approvals/delegation` — delegation `from→to`
- `POST /v1/approvals/sla/check` — trigger SLA 12h escalation
- `POST /v1/documents` — ingesta RAG con `gcs_uri` support
- `GET /v1/rag/search` — hybrid retrieval + reranker + `use_reranker` flag
- `POST /v1/rag/feedback` / `GET /v1/rag/feedback` — feedback loop
- `POST /v1/webhooks/subscriptions` — webhook subscriptions `events=[execution.completed, approval.requested]` con `HMAC sha256`, `X-Webhook-Id`, retry exponencial
- `GET /v1/webhooks/subscriptions` / `DELETE /v1/webhooks/subscriptions/{id}`
- `GET /healthz`, `GET /readyz`, `GET /slo`, `GET /metrics` — observabilidad
- OpenAPI strict lint `tools/openapi_lint.py` — Spectral 0 errors, breaking-change check con `x-compatible`, `docs/api/openapi.json`
- Python SDK `sdk/python` — `ProcurementClient` con retries + `Idempotency-Key` auto
- TypeScript SDK `sdk/ts` — `ProcurementClient` con `msw` tests
- Webhook delivery via `outbox_events` drainer con `HMAC`, `X-Webhook-Id`, retry
- Pagination stable: `total_count`, `page_size`, `has_more`, orden `timestamp asc, event_id asc`

### Changed
- `GET /v1/procurement/executions/{id}/events` ahora incluye `total`, `limit`, `has_more`, `next_cursor` estables
- `POST /v1/procurement/executions` ahora responde `202` con `approval_request` incluido
- RateLimit headers `RateLimit-*` y `Retry-After`

### Fixed
- Cursor pagination estable con `event_id` asc para evitar duplicados en mismo timestamp
- Scope hash validation 409 `scope_mismatch`/`expired` con `Idempotency-Key` idempotente

### Security
- JWT `Authorization: Bearer` + `X-API-Key` fallback, RBAC `approver/admin`, tenant isolation, PII redact, payload 256KB

### Compatibility
- `x-compatible: v1` — breaking changes requieren bump `version` a `0.2.0` y entrada en changelog
- No breaking changes desde `0.1.0` — todas las adiciones son backward compatible (nuevos endpoints, nuevos campos opcionales)

---

## Cómo hacer breaking change

1. Bump `version` en `src/procurement_platform/api/main.py` (`app.version`) y `docs/api/openapi.json` `info.version`
2. Añadir entrada `## [0.2.0] - YYYY-MM-DD` con `### Breaking` y razón
3. Correr `python tools/openapi_lint.py --generate --check --fail-on-breaking` — debe pasar
4. Actualizar SDKs (`sdk/python`, `sdk/ts`) y `examples/`
5. CI `tools/openapi_lint.py --check --fail-on-breaking` bloqueará PR sin bump

## Generación

```bash
python tools/openapi_lint.py --generate
python tools/openapi_lint.py --check
make openapi-check
make openapi-generate
```
