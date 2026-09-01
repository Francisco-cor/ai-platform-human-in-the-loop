# Runbook — Approval stuck / backlog alto (F5-2/5)

**Alerta:** `ApprovalBacklogHigh >50` o `approval_pending_total` crece.

## Diagnóstico

1. **Métricas:** `curl /metrics | grep approval_pending` → `approval_pending_total{tenant="..."}`.
2. **DB:** `SELECT tenant_id, COUNT(*) FROM workflow_executions WHERE status='AWAITING_APPROVAL' GROUP BY tenant_id;`
3. **SLO:** `curl /slo` → `approval_backlog` y `burn_rate`.
4. **Audit:** `GET /v1/procurement/executions/{id}/events` → busca `approval.requested` y `duration_ms` / `age`.
5. **Grafana:** dashboard panel `Backlog aprobaciones` — si >50 por 5m, dispara alerta.

## Causas

- Aprobador sin rol `approver` (403) → `security/rbac.py` `has_role`.
- `scope_hash` mismatch / `expired` (409) → `approvals/service.py` `validate_scope_or_raise`.
- Redis lock no libera → `infra/locks/manager.py`.
- Notificación no llega (F7) — ver `notifications/service.py` (futuro).

## Acciones

- **Escalar:** `POST /v1/approvals/{id}/decision` con `approver_02` (doble aprobación si `risk high`).
- **Reanudar:** `POST /v1/procurement/executions/{id}/resume` idempotente.
- **Limpiar:** `UPDATE workflow_executions SET status='EXPIRED' WHERE approval_request->>'expires_at' < now()` (auto-expira en `GET /v1/approvals/{id}`).
- **Métrica:** `approval_age_seconds` gauge — si >12h, escalar según `approvals/service.py` SLA.

**Prevención:** dashboard + alerta `approval_pending_total >50` → PagerDuty (F10).

