# Procurement UI — Approval Inbox (Fase 7 HITL)

Next.js 14 inbox para aprobaciones humanas. Cumple §11 y criterio Fase 7: aprobador ve propuesta exacta, líneas, total, evidencia RAG, políticas, riesgo y decide en <2 min.

## Estructura

- `app/page.tsx` — inbox list (filtra pending/approved, via `GET /v1/approvals/export?state=pending`)
- `app/approvals/[id]/page.tsx` — detail con `ApprovalCard` + `ScopeDiff` + botones aprobar/rechazar/pedir cambios
- `app/executions/[id]/page.tsx` — timeline `request_id→trace_id` con `GET /v1/procurement/executions/{id}/events?format=trace`
- `components/ScopeDiff.tsx` — diff `proposal_snapshot` vs `proposal_current` si `scope_mismatch`
- `components/Timeline.tsx` — audit timeline con links a Grafana

## API proxy

`lib/api.ts` consume `NEXT_PUBLIC_API_BASE` (default `http://localhost:8000`). Maneja `Idempotency-Key` y `scope_hash` validation (409).

## Desarrollo

```bash
cd ui
npm install
npm run dev # http://localhost:3001
# con API corriendo:
# API http://localhost:8000
```

`Makefile:ui-dev` = `cd ui && npm run dev`
`make ui-build` = `cd ui && npm run build`

## Test e2e

```bash
npx playwright install
npm run test:e2e # ui/tests/approval.spec.ts
# requiere API + UI levantados
```

Cubre: happy → approve → COMPLETED, malicious → BLOCKED, scope_diff 409.

## Notificaciones

Backend dispara `notifications/service.py` en `approval.requested` con `scope_hash` truncado y link inbox. Email/Slack/Webhook configurables via env.

## SLA y delegación

Ver `src/procurement_platform/approvals/service.py` — `check_approval_sla` cada 15m, escalamiento a `manager_01` tras 12h, delegación `approver_01 → delegate_01`.

## Bulk y export

`POST /v1/approvals/bulk/decision` + `GET /v1/approvals/export?tenant=&state=` (CSV). RBAC `admin`.

## Demo

Ver `docs/demos/demo_script.md` — happy vs malicious via UI.
