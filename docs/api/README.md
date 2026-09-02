# API Docs — Procurement Platform (Fase 11)

**OpenAPI:** `docs/api/openapi.json` (35 paths, Spectral 0). Generado via `tools/openapi_lint.py --generate --check`.

## Endpoints principales

| Método | Path | Propósito |
|--------|------|-----------|
| POST | `/v1/procurement/executions` | Crear ejecución (Idempotency-Key, RateLimit 60/min, Payload 256KB) |
| GET | `/v1/procurement/executions/{id}` | Estado + proposal + approval |
| GET | `/v1/procurement/executions/{id}/events?cursor=&limit=` | Audit paginado `total/has_more/next_cursor` |
| GET | `/v1/procurement/executions/{id}/timeline?at=` | Time-travel |
| POST | `/v1/approvals/{id}/decision` | approved/rejected/needs_changes |
| GET | `/v1/approvals/{id}` | snapshot + scope_hash |
| POST | `/v1/expense/executions` | Expense (Fase 11) `amount, currency, reason` → AWAITING → COMPLETED reusa platform |
| GET | `/v1/expense/executions/{id}` | Expense estado |
| POST | `/v1/documents` | Ingesta RAG (gcs_uri o content) |
| GET | `/v1/rag/search?query=&tenant_id=` | Citas `citation, score, rerank_score` |
| POST | `/v1/rag/feedback` | thumbs up/down |
| GET | `/v1/lineage?document_id=` | Ejecuciones afectadas por doc |
| POST | `/v1/bq/drain` | Drena outbox → BQ fake |
| GET | `/v1/bq/query` | Fake BQ query |
| GET | `/v1/flags` | Feature flags |
| POST | `/v1/retention/run` | Retention job |
| DELETE | `/v1/tenants/{id}/data` | GDPR soft-delete |
| POST | `/v1/secrets/{id}/rotate` | Rotation audit (WIF) |
| GET | `/v1/secrets/rotation/status` | Workload identity check |
| POST | `/v1/webhooks/subscriptions` | Webhook HMAC |
| GET | `/healthz`, `/readyz`, `/metrics`, `/slo` | Ops |

## SDKs

- **Python:** `pip install -e sdk/python` → `from procurement_sdk import ProcurementClient; c=ProcurementClient(base_url="http://localhost:8000"); c.create_execution(...)`
- **TS:** `cd sdk/ts && npm install && npm test` (msw)

Ver `sdk/python/tests/test_client.py` y `sdk/ts/tests`.

## Postman

Import `docs/api/postman_collection.json`, variable `baseUrl=http://localhost:8000`.

## Versionado

`/v1` — breaking changes requieren bump + `tools/openapi_lint.py --check --fail-on-breaking` en CI.

## Auth

`Authorization: Bearer <JWT>` (JWKS) o `X-API-Key` fallback local. RBAC `approver, admin`. Tenant isolation via `tenant_id`.

## Rate limit & payload

`60/min` por tenant+IP, `256KB` payload, `Idempotency-Key` global.

## Ejemplos

Ver `examples/curl_happy.sh`, `examples/sdk_happy.py`, `examples/sdk_ts_happy.ts`.

## Contract tests

`pytest tests/contract -v` + `python tools/openapi_lint.py --check`.

