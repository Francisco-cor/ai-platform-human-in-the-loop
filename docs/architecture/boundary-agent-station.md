# Boundary con Agent Station — Contrato de Integración Externa (Fase 0)

> **Estado:** Baseline v0.1 — 2026-08-20  
> **Objetivo Fase 0:** Definir exclusivamente el contrato necesario para comunicarse con Agent Station sin reutilizar módulos internos, bases de datos, runtime, clases ni infraestructura privada.

## 1. Principio de frontera

Este repositorio es **una plataforma independiente**. Agent Station es un **sistema externo**.

```
┌─────────────────┐         HTTPS + JSON          ┌──────────────────────────┐
│  Agent Station  │ ───────────────────────────►  │  procurement-platform    │
│  (externo)      │ ◄───────────────────────────  │  FastAPI / v1            │
└─────────────────┘   callbacks / webhooks (opt) └──────────────────────────┘
```

Reglas inviolables (sección 0 del Plan):

1. Comunicación **exclusivamente** por APIs versionadas (`/v1`) y, si aplica, eventos documentados.
2. **No** se comparte base de datos, runtime, clases, cola ni secretos.
3. DTOs externos **separados** de modelos internos del dominio; traducción explícita en el adapter.
4. Propagación de correlación `request_id`, `execution_id`, `trace_id` cuando el contrato lo soporte.

## 2. Matriz de endpoints y capacidades

### 2.1 Endpoints expuestos por `procurement-platform` (consumidos por Agent Station)

| Método | Path | Propósito | Auth | Idempotencia | Rate limit sugerido |
|---|---|---|---|---|---|
| `POST` | `/v1/procurement/executions` | Crear ejecución a partir de intent natural o solicitud estructurada | Bearer (AgentStation SA) | `Idempotency-Key` header requerido | 100 req/s por tenant |
| `GET` | `/v1/procurement/executions/{execution_id}` | Consultar estado, resumen y `scope_hash` | Bearer | — | 500 req/s |
| `GET` | `/v1/procurement/executions/{execution_id}/events` | Consultar audit events visibles para el caller (paginado) | Bearer | — | 200 req/s |
| `POST` | `/v1/approvals/{approval_id}/decision` | Aprobar / rechazar / pedir cambios (human-in-the-loop puede ser proxied por Agent Station) | Bearer + RBAC | `Idempotency-Key` | 50 req/s |
| `POST` | `/v1/documents` | Registrar documento para ingesta RAG (opcional en MVP) | Bearer | SHA256 dedup | 20 req/s |
| `GET` | `/healthz` | Liveness | none | — | — |
| `GET` | `/readyz` | Readiness (DB, Redis) | none | — | — |

**Versionado:** prefijo `/v1`. Breaking changes → `/v2` con periodo de deprecación. `Accept: application/json; version=1.0`.

### 2.2 Endpoints consumidos por `procurement-platform` hacia Agent Station (si aplica)

La plataforma **no asume** que Agent Station exista en local. El cliente es **configurable** y opcional. Cuando se habilita:

| Método | Path (Agent Station) | Propósito | Notas |
|---|---|---|---|
| `POST` | `{AGENT_STATION_BASE_URL}/v1/callbacks/execution-update` | Notificar cambio de estado (`AWAITING_APPROVAL`, `COMPLETED`, `FAILED_*`) | Retry con backoff + outbox; no bloquea workflow |
| `POST` | `{AGENT_STATION_BASE_URL}/v1/events` | Publicar evento `audit` opcional | Best-effort, HMAC firmado si se configura |
| `GET` | `{AGENT_STATION_BASE_URL}/v1/health` | Verificar reachability (para `/readyz` extendido) | Timeout 2s |

> Si Agent Station solo opera en modo **request/response** (sin callbacks), la notificación es omitida y Agent Station debe hacer polling a `GET /executions/{id}`.

### 2.3 Esquemas (resumen, ver `src/procurement_platform/integrations/agent_station/dtos.py`)

**POST /v1/procurement/executions — Request (desde Agent Station)**

```json
{
  "request_id": "req_01",
  "tenant_id": "tenant_demo",
  "requester_id": "user_01",
  "raw_intent": "Necesitamos reponer materiales críticos para las próximas tres semanas.",
  "items": [{"sku": "MAT-001", "quantity": 120, "unit": "piece"}],
  "horizon_days": 21,
  "location_id": "warehouse_north",
  "currency": "USD",
  "source": "agent_station",
  "idempotency_key": "idem_abc123"
}
```

Campos `items`, `horizon_days`, `location_id` son opcionales si `raw_intent` viene solo; el nodo `normalize_request` los infiere y valida.

**Response 202 Accepted**

```json
{
  "execution_id": "exec_01",
  "request_id": "req_01",
  "status": "RECEIVED",
  "created_at": "2026-01-01T00:00:00Z"
}
```

**GET /v1/procurement/executions/{id} — Response**

```json
{
  "execution_id": "exec_01",
  "request_id": "req_01",
  "tenant_id": "tenant_demo",
  "status": "AWAITING_APPROVAL",
  "current_node": "wait_for_human_decision",
  "proposal": { "...": "Proposal completo con scope_hash" },
  "approval_request": { "approval_id": "appr_01", "expires_at": "..." },
  "created_at": "...",
  "updated_at": "..."
}
```

**POST /v1/approvals/{approval_id}/decision**

```json
{
  "decision": "approved | rejected | needs_changes",
  "decided_by": "approver_01",
  "reason": "Within delegated budget",
  "idempotency_key": "idem_xyz"
}
```

### 2.4 Autenticación

| Lado | Mecanismo Fase 0-1 | Futuro |
|---|---|---|
| Agent Station → Platform | `Authorization: Bearer <AGENT_STATION_API_TOKEN>` validado contra `Secret Manager` / env. Scope `tenant:write` | mTLS o Workload Identity Federation en GCP |
| Platform → Agent Station | `Authorization: Bearer <PLATFORM_CALLBACK_TOKEN>` + opcional `X-Signature: HMAC-SHA256` | mTLS |
| Humano aprobador | Proxy via Agent Station o directo con JWT del IdP del tenant; RBAC `approver` | Re-auth step-up para alto riesgo |

Tokens nunca se loguean en claro; solo `token_hash` truncado en `audit_events`.

### 2.5 Errores, paginación y limites

Formato de error estándar:

```json
{
  "code": "validation_error | not_found | conflict | unauthorized | rate_limited | blocked_by_policy",
  "message": "Human readable (sin secretos)",
  "request_id": "req_...",
  "details": {}
}
```

- `429` con `Retry-After`.
- Paginación cursor-based para `/events`: `?cursor=...&limit=50` (max 100).
- Payload max 256 KB (rechazo `413`).
- Headers de correlación: `X-Request-Id`, `X-Execution-Id`, `traceparent` (W3C).

## 3. Modos de comunicación

1. **Request/Response sincrónico** (MVP): Agent Station crea ejecución y hace polling. Simple, sin infraestructura extra.
2. **Callback asíncrono** (opt-in): Platform notifica a Agent Station vía `POST /callbacks/execution-update` con retry y circuit breaker. Útil para UX reactiva.
3. **Webhooks / Event bus** (futuro): Agent Station se suscribe a tópicos; no requerido para Fase 0-1.

Decisión Fase 0: soportar **(1) siempre + (2) opcional** configurable por `AGENT_STATION_CALLBACK_ENABLED=false` por defecto. El workflow no depende de que el callback tenga éxito.

## 4. Garantías de idempotencia y reintentos

- `Idempotency-Key` obligatorio en `POST /executions` y `POST /approvals/.../decision`. Almacenado en Redis (TTL 24h) + Postgres (`idempotency_keys`). Replay devuelve mismo `execution_id` / resultado sin duplicar efecto.
- Retries del cliente `AgentStationClient`: solo errores transitorios (`429`, `502`, `503`, `504`, timeout). Backoff exponencial con jitter, max 3 intentos, circuit breaker (5 fallos → open 30s).
- Callbacks usan `outbox_events` para no perder notificación tras reinicio.

## 5. Qué NO cruza el boundary

- No acceso a `workflow_checkpoints`, `inventory_items`, `policy_versions` de Agent Station.
- No lectura de trazas internas ni prompts completos.
- No escritura directa en tablas de la plataforma.
- Documentos se intercambian solo vía `POST /documents` o GCS presigned URL, nunca filesystem compartido.

## 6. Gaps y riesgos identificados (Fase 0)

| Gap | Impacto | Mitigación |
|---|---|---|
| Contrato real de Agent Station no publicado | DTOs asumidos pueden divergir | Cliente aislado + fake + contract tests; cambiar solo DTOs externos |
| Autenticación real de Agent Station desconocida | Token genérico puede no ser suficiente | Adapter con strategy (`Bearer`, `HMAC`, `mTLS`); config por env |
| Event ordering si Agent Station espera exactamente-once | Callbacks at-least-once | Idempotency-Key + `event_id` deduplicación en consumidor |
| PII en `raw_intent` | Fuga en logs | Redacción en `audit_events`; no persistir PII cruda en BigQuery |
| LangGraph vs runtime propio | Checkpoint durability | Abstracción `WorkflowOrchestrator` (ver decisión 0002) |

## 7. Artefactos entregados en Fase 0

- `src/procurement_platform/integrations/agent_station/client.py` — cliente HTTP aislado.
- `src/procurement_platform/integrations/agent_station/dtos.py` — DTOs externos versionados.
- `src/procurement_platform/integrations/agent_station/fake.py` — fake in-memory + servidor FastAPI opcional para `docker-compose`.
- `tests/contract/test_agent_station_client.py` — contract tests contra fake.
- Este documento + `docs/decisions/0001-boundary-agent-station.md`.

Criterio de salida Fase 0: **un nuevo colaborador puede levantar `docker-compose up` con el fake y explicar qué llamadas cruzan el boundary, qué datos se intercambian y quién es responsable de cada estado** — ver `README.md` y `Makefile`.
