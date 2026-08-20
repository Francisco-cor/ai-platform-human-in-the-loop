# ADR 0007 — Human approval y ejecución simulada Fase 5 (snapshot, scope_hash, reanudación durable)

**Fecha:** 2026-08-20  
**Estado:** Aceptada  
**Fase:** 5

## Contexto

Fase 5 exige completar el flujo con pausa humana, decisión, reanudación durable y acción idempotente. Debe demostrar que nunca se ejecuta una orden sin aprobación vigente, que un retry/reanudación no duplica, y que expiración, rechazo, cambios y scope_hash se validan con auditoría.

## Decisión

**Domain models** (`domain/models.py:221`): `ApprovalRequest` extendido con `proposal_snapshot` (dict inmutable de la propuesta al momento de solicitar), `risk_level`, `total`, `currency`, `required_approvals` (1 por defecto, 2 si `risk==high` o `medium+total>3000`), `approvals_received`, `approvers`, helpers `is_expired()`, `is_scope_valid(proposal)`, `can_decide()`. `compute_scope_hash` ya existía en `Proposal`.

**Approvals service** (`approvals/service.py:10`): `ApprovalService` helpers `create_approval_request()` con snapshot + `scope_hash` + expiración 24h, `compute_required_approvals()`, `find_execution_by_approval_id()`, `is_expired()`, `check_and_expire()` (transiciona a `EXPIRED` si `now>expires_at`, audit `approval.expired`), `validate_scope_or_raise()` (compara `scope_hash` actual vs aprobación, bloquea `scope_mismatch`), `decide_approval()` con lock por `execution_id`, expiración, validación scope, manejo doble aprobación (si `approvals_received < required` → `partially_approved` sin ejecutar; si completo → `approved`), idempotencia via `IdempotencyKey` y `approvers` list.

**Locks e idempotencia**: `approvals/service.py` y `workflows/orchestrator.py` usan `threading.Lock` por `execution_id` (`_execution_locks`) y `tools/gateway.py` usa `_GLOBAL_IDEMPOTENCY` + `_GATEWAY_LOCKS` por `idempotency_key` (`sha256(execution_id+tool+payload)`) para garantizar que dos reintentos concurrentes no dupliquen `submit_purchase_order`. En producción el lock sería Redis redlock y la idempotencia Postgres/Redis; en local el store global simula durabilidad intra-proceso y sobrevive a re-instancias de `ToolGateway`.

**Orchestrator** (`workflows/orchestrator.py:207`): 
- `_execution_locks` global + `_acquire_execution_lock()`/`_release_execution_lock()`.
- `advance_synthetic()` ahora crea `ApprovalRequest` via `create_approval_request()` con snapshot y audit `approval.requested`.
- `_check_and_expire_if_needed()` auto-expira al consultar (`GET /executions/{id}` y `GET /approvals/{id}` lo invocan).
- `_safe_transition()` helper idempotente (si ya en destino, no hace nada).
- `_execute_purchase_order_if_needed()` verifica `approval.status==approved`, `!is_expired`, `is_scope_valid`, llama `ToolGateway.call(submit_purchase_order, has_approval=True, state=APPROVED)`, persiste `PurchaseOrder` por línea, audit `tool.submit_purchase_order.completed`, checkpoint. Si ya `ACTION_EXECUTED/VERIFIED/COMPLETED` → no-op idempotente.
- `resume_durable()` para reanudación tras reinicio/timeout: si `AWAITING_APPROVAL` con `approved` → `APPROVED → ACTION_EXECUTED → VERIFIED → COMPLETED` idempotente; si `APPROVED` sin ejecutar → ejecuta; si `EXPIRED/BLOQUEADO` → no ejecuta. Verifica `scope_mismatch` y bloquea a `BLOCKED` si propuesta cambió.
- `approve_and_complete()` con lock, expiración, `scope_mismatch` (audit `approval.scope_mismatch`), doble aprobación (`partially_approved` audit, permanece `AWAITING_APPROVAL`), luego `APPROVED → ACTION_EXECUTED → VERIFIED → COMPLETED` via `_execute_purchase_order_if_needed()`. Maneja idempotencia para `COMPLETED` (mismo `decided_by` → 200; distinto → 409).
- `reject_execution()` y `request_changes()` con locks, expiración, audit y transición a `REJECTED`/`NEEDS_CLARIFICATION`.

**Tool Gateway Fase 5** (`tools/gateway.py:62`): store global `_GLOBAL_IDEMPOTENCY`/`_GLOBAL_CALL_LOG` compartido entre instancias, lock por key `_gateway_lock()`, doble-check idempotente, presupuesto y validación igual que Fase 4. `submit_purchase_order` requiere `has_approval=True` else `approval_required`.

**API** (`api/main.py:250`):
- `GET /v1/approvals/{approval_id}` retorna snapshot inmutable, `proposal_snapshot` vs `proposal_current`, `risk_level/total/required_approvals/approvers/expires_at/execution_status`; auto-expira al consultar.
- `POST /v1/approvals/{approval_id}/decision` ahora valida `scope_hash` opcional del cliente, delega a `orchestrator.approve_and_complete`/`reject_execution`/`request_changes`, mapea `ValueError` a `409 expired/scope_mismatch/already_decided/conflict/invalid_state`, respeta `Idempotency-Key` (scope `approval_decision`) y retorna `partially_approved` si falta segunda aprobación, `approved/COMPLETED` si completa, `rejected/REJECTED`, `needs_changes/NEEDS_CLARIFICATION`.
- `GET /v1/procurement/executions/{id}` auto-expira antes de retornar.
- `POST /v1/procurement/executions/{id}/resume` reanudación durable idempotente (`AWAITING_APPROVAL→APPROVED→...` o noop si ya terminal).
- `POST /v1/procurement/executions` ya creaba `ApprovalRequest` con snapshot (vía orchestrator).

**Persistencia**: `proposal_snapshot` dentro de `approval_request` JSON (sin migración nueva); `PurchaseOrder` se persiste tras `submit_purchase_order` para verificación post-acción.

## Consecuencias

- Snapshot inmutable garantiza que el aprobador ve el objeto exacto que se ejecutará; `scope_hash` (`proposal_id+supplier_id+lines+total+currency`) liga aprobación a propuesta; si cambia, se requiere nueva aprobación (409 `scope_mismatch`).
- Expiración 24h por defecto, audit `approval.expired`, transición a `EXPIRED`, no se puede aprobar expirada.
- Rechazo es terminal (`REJECTED`), `needs_changes` → `NEEDS_CLARIFICATION`, no reintento automático.
- Doble aprobación: `risk==high` o `medium+total>3000` → `required_approvals=2`; primera aprobación deja `pending` con `partially_approved`, segunda completa y ejecuta; idempotente por `approvers` list.
- Nunca se ejecuta sin aprobación vigente: gateway exige `has_approval`, orchestrator verifica `is_scope_valid` y `!is_expired` antes de `submit_purchase_order`; `AWAITING_APPROVAL` no permite tools de escritura (allowlist vacío).
- Reanudación durable: `resume_durable()` y `/_resume` permiten continuar tras reinicio sin duplicar orden; gateway idempotency + `execution_lock` + check `status >= ACTION_EXECUTED` garantizan `duplicate_action_rate == 0`.
- Auditoría correlacionada: `approval.requested`, `approval.partially_approved`, `approval.decided`, `approval.expired`, `approval.scope_mismatch`, `tool.submit_purchase_order.completed`, `execution.transition.*`, `node.*.completed`, `WorkflowCheckpoint` por nodo.
- NEXT: Fase 6 evaluation layer v1.

## Alternativas descartadas

- Aprobar sin snapshot: se descartó por riesgo de `changed_after_approval`; snapshot resuelve.
- Lock solo en API: se movió a orchestrator + gateway para cubrir reintento interno y concorrencia.
- Expiración solo por cron: se hace eager al consultar/decidir para determinismo en tests.
- Doble aprobación solo por total: se combinó `risk_level` + umbral para cubrir casos `high` con total bajo.
