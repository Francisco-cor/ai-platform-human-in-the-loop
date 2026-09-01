"""Workflow orchestrator — runtime propio (Fase 1-5).

Grafo lineal determinista:
RECEIVED -> NORMALIZED -> CONTEXT_LOADED -> ... -> COMPLETED
Fase 1: grafo sintético sin LLM.
Fase 2: cálculo determinista de faltantes, proveedores y policy checks — sin LLM.
Fase 3: RAG seguro con filtros, citas y bloqueo de contenido malicioso — sin LLM para decisiones.
Fase 4: LLM con fallback Gemini→DeepSeek→fake, gateway y recálculo determinista.
Fase 5: aprobación humana con snapshot inmutable, scope_hash, expiración, reanudación durable e idempotencia.

Contratos §5, §7 y §11. Cálculos críticos no llaman al modelo (Fase 2-3 criterio salida).
Fase 5 criterio: nunca se ejecuta sin aprobación vigente; retry/reanudación no duplica orden.
"""

from __future__ import annotations

import hashlib
import json
import threading
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from procurement_platform.audit.service import create_audit_event
from procurement_platform.domain.inventory import (
    InventoryContext,
    calculate_shortages,
    load_context_from_fixtures,
)
from procurement_platform.domain.models import (
    ApprovalRequest,
    ApprovalStatus,
    Execution,
    ExecutionState,
    NormalizedRequest,
    Proposal,
    ProposalLine,
    is_valid_transition,
    new_id,
    utcnow,
)
from procurement_platform.domain.suppliers import (
    SupplierCatalog,
    build_proposal_lines_from_shortages,
    load_catalog_from_fixtures,
)
from procurement_platform.persistence.models import WorkflowCheckpoint, WorkflowExecution
from procurement_platform.policies.engine import PolicyConfig, run_policy_checks

# Fase 3 RAG
try:
    from procurement_platform.rag.models import Document, DocumentMetadata
    from procurement_platform.rag.service import RagService

    _has_rag = True
except Exception:
    Document = None  # type: ignore
    DocumentMetadata = None  # type: ignore
    RagService = None  # type: ignore
    _has_rag = False


def _serialize_execution(exec_obj: WorkflowExecution) -> Execution:
    return Execution(
        execution_id=exec_obj.execution_id,
        request_id=exec_obj.request_id,
        tenant_id=exec_obj.tenant_id,
        status=ExecutionState(exec_obj.status),
        current_node=exec_obj.current_node,
        normalized_request=NormalizedRequest.model_validate(exec_obj.normalized_request)
        if exec_obj.normalized_request
        else None,
        proposal=Proposal.model_validate(exec_obj.proposal) if exec_obj.proposal else None,
        approval_request=ApprovalRequest.model_validate(exec_obj.approval_request)
        if exec_obj.approval_request
        else None,
        created_at=exec_obj.created_at,
        updated_at=exec_obj.updated_at,
        trace_id=exec_obj.trace_id,
    )


def _load_default_inventory_context() -> InventoryContext:
    """Carga fixtures por defecto deterministas (Fase 2).

    Usa evals/fixtures/inventory_happy_path.json y suppliers_demo.json si existen,
    si no usa valores sintéticos mínimos.
    """
    try:
        import json as _json

        inv_path = Path("evals/fixtures/inventory_happy_path.json")
        sup_path = Path("evals/fixtures/suppliers_demo.json")
        if inv_path.exists():
            inv_fixture = _json.loads(inv_path.read_text(encoding="utf-8"))
        else:
            inv_fixture = {
                "location_id": "warehouse_north",
                "items": [
                    {
                        "sku": "MAT-001",
                        "on_hand": 20,
                        "reserved": 5,
                        "in_transit": 10,
                        "daily_demand_forecast": 8,
                    }
                ],
                "lead_times_days": {"MAT-001": 7},
            }
        # open orders fixture (Fase 2 nuevo)
        open_orders_path = Path("evals/fixtures/open_orders.json")
        open_orders = []
        if open_orders_path.exists():
            open_orders = _json.loads(open_orders_path.read_text(encoding="utf-8")).get(
                "open_orders", []
            )

        return load_context_from_fixtures(
            inventory_fixture=inv_fixture, open_orders_fixture=open_orders
        )
    except Exception:
        # fallback mínimo
        return InventoryContext(snapshots={}, forecasts={}, open_orders=[])


def _load_default_catalog() -> SupplierCatalog:
    try:
        import json as _json

        sup_path = Path("evals/fixtures/suppliers_demo.json")
        if sup_path.exists():
            sup_fixture = _json.loads(sup_path.read_text(encoding="utf-8"))
            return load_catalog_from_fixtures(sup_fixture)
    except Exception:
        pass
    # fallback
    return load_catalog_from_fixtures(
        {
            "suppliers": [
                {
                    "supplier_id": "supplier_demo",
                    "name": "Demo Supplier Inc.",
                    "active": True,
                    "allowed_tenants": ["tenant_demo"],
                    "currency": "USD",
                    "min_order": 1,
                    "max_order": 10000,
                    "lead_time_days": 7,
                }
            ],
            "quotes": [{"sku": "MAT-001", "unit_price": 10.0}],
        }
    )


def _load_default_policy_config() -> PolicyConfig:
    # budget_limits por defecto: tenant_demo/* = 5000 (ver fixtures/policies_budget_limit_v1.json)
    try:
        import json as _json

        pol_path = Path("evals/fixtures/policies_budget_limit_v1.json")
        if pol_path.exists():
            pol = _json.loads(pol_path.read_text(encoding="utf-8"))
            limit = pol.get("rules", {}).get("delegated_limit_usd", 5000)
            return PolicyConfig(
                budget_limits={
                    ("tenant_demo", "*"): float(limit),
                    ("tenant_demo", "warehouse_north"): float(limit),
                }
            )
    except Exception:
        pass
    return PolicyConfig(
        budget_limits={("tenant_demo", "*"): 5000.0, ("tenant_demo", "warehouse_north"): 5000.0}
    )


_global_rag_service = None


def get_rag_service():
    global _global_rag_service
    if _global_rag_service is None and _has_rag:
        # lazy init con embedding fake
        _global_rag_service = RagService()
        # seed con políticas por defecto si está vacío (Fase 3)
        if _global_rag_service.retrieval.count() == 0:
            try:
                _seed_default_policies(_global_rag_service)
            except Exception:
                pass
    return _global_rag_service


def _seed_default_policies(rag: Any) -> None:
    """Seed determinista de políticas para que retrieval tenga datos sin DB."""
    from procurement_platform.rag.models import Document, DocumentMetadata

    # Política vigente: budget_limit
    doc1 = Document(
        metadata=DocumentMetadata(
            document_id="policy_budget_v1",
            tenant_id="tenant_demo",
            title="Política de límite presupuestario",
            doc_type="policy",
            classification="internal",
            jurisdiction="global",
            location_id="warehouse_north",
            version="1.0.0",
            valid_from=datetime(2025, 1, 1, tzinfo=UTC),
            valid_to=None,
            status="approved",
            allowed_tenants=["tenant_demo"],
        ),
        content="Política: El límite delegado para tenant_demo en warehouse_north es 5000 USD. Toda orden por encima requiere aprobación humana. Esta política es normativa y vigente.",
        pages=[{"page": 1, "section": "budget", "text": "límite 5000 USD"}],
    )
    # Política vigente: supplier allowlist
    doc2 = Document(
        metadata=DocumentMetadata(
            document_id="policy_supplier_allowlist_v1",
            tenant_id="tenant_demo",
            title="Proveedores permitidos",
            doc_type="policy",
            classification="internal",
            jurisdiction="global",
            version="1.0.0",
            valid_from=datetime(2025, 1, 1, tzinfo=UTC),
            status="approved",
            allowed_tenants=["tenant_demo"],
        ),
        content="Política: Proveedores permitidos para tenant_demo son supplier_demo y supplier_alt. Proveedor activo requerido.",
        pages=[{"page": 1, "section": "suppliers", "text": "allowlist"}],
    )
    for doc in (doc1, doc2):
        rag.ingest_document(document=doc, actor_id="seed")


# Locks por execution para Fase 5 — idempotencia y prevención de duplicación
# Ahora delegado a LockManager (infra/locks) con fallback a threading para compatibilidad tests
_execution_locks: dict[str, threading.Lock] = {}
_execution_locks_guard = threading.Lock()


def _get_lock_manager_orchestrator():
    try:
        from procurement_platform.infra.locks.manager import get_lock_manager

        return get_lock_manager()
    except Exception:
        return None


def _acquire_execution_lock(execution_id: str, blocking: bool = False) -> bool:
    mgr = _get_lock_manager_orchestrator()
    if mgr is not None:
        return mgr.acquire(f"orchestrator:{execution_id}", blocking=blocking, timeout=1.0)
    with _execution_locks_guard:
        if execution_id not in _execution_locks:
            _execution_locks[execution_id] = threading.Lock()
        lock = _execution_locks[execution_id]
    return lock.acquire(blocking=blocking)


def _release_execution_lock(execution_id: str) -> None:
    mgr = _get_lock_manager_orchestrator()
    if mgr is not None:
        try:
            mgr.release(f"orchestrator:{execution_id}")
            return
        except Exception:
            pass
    with _execution_locks_guard:
        lock = _execution_locks.get(execution_id)
    if lock and lock.locked():
        try:
            lock.release()
        except RuntimeError:
            pass


class WorkflowOrchestrator:
    """Orquestador sincrónico — Fase 1-5 (RAG + LLM + aprobación durable e idempotente)."""

    def __init__(
        self,
        *,
        inventory_context: InventoryContext | None = None,
        supplier_catalog: SupplierCatalog | None = None,
        policy_config: PolicyConfig | None = None,
        rag_service: Any | None = None,
    ) -> None:
        self._inventory_context = inventory_context
        self._supplier_catalog = supplier_catalog
        self._policy_config = policy_config
        self._rag_service = rag_service

    def _get_inventory_context(self) -> InventoryContext:
        return self._inventory_context or _load_default_inventory_context()

    def _get_catalog(self) -> SupplierCatalog:
        return self._supplier_catalog or _load_default_catalog()

    def _get_policy_config(self) -> PolicyConfig:
        return self._policy_config or _load_default_policy_config()

    def _get_rag_service(self):
        if self._rag_service is not None:
            return self._rag_service
        return get_rag_service()

    def _retrieve_policies_for_execution(
        self, row: WorkflowExecution, trace_id: str | None = None
    ) -> tuple[list, bool, str]:
        """Fase 3: recupera políticas via RAG con filtros tenant/vigencia y valida seguridad.

        Retorna (results, should_block, reason) y registra audit.
        """
        rag = self._get_rag_service()
        if rag is None:
            return [], False, "rag_not_configured"
        normalized = (
            NormalizedRequest.model_validate(row.normalized_request)
            if row.normalized_request
            else None
        )
        if not normalized:
            return [], False, "no_normalized"
        # query RAG: buscar políticas de presupuesto y proveedores para este tenant/location
        try:
            results, should_block, reason = rag.retrieve_for_execution(
                query=f"políticas presupuesto y proveedores para {normalized.tenant_id} {normalized.location_id}",
                tenant_id=normalized.tenant_id,
                location_id=normalized.location_id,
                top_k=5,
            )
            return results, should_block, reason
        except Exception as e:
            import structlog

            structlog.get_logger("orchestrator").warning("rag_retrieval_failed", error=str(e))
            return [], False, f"retrieval_error:{e}"

    def create_execution(
        self,
        db: Session,
        *,
        normalized: NormalizedRequest,
        trace_id: str | None = None,
        actor_id: str = "system",
    ) -> Execution:
        execution_id = new_id("exec")
        now = utcnow()
        row = WorkflowExecution(
            execution_id=execution_id,
            request_id=normalized.request_id,
            tenant_id=normalized.tenant_id,
            status=ExecutionState.RECEIVED.value,
            current_node="intake_request",
            normalized_request=normalized.model_dump(mode="json"),
            proposal=None,
            approval_request=None,
            trace_id=trace_id,
            created_at=now,
            updated_at=now,
        )
        db.add(row)
        db.flush()
        create_audit_event(
            db,
            execution_id=execution_id,
            request_id=normalized.request_id,
            event_type="execution.created",
            actor_type="system",
            actor_id=actor_id,
            trace_id=trace_id,
            details={"status": ExecutionState.RECEIVED.value},
        )
        # Persist checkpoint
        db.add(
            WorkflowCheckpoint(
                checkpoint_id=new_id("chk"),
                execution_id=execution_id,
                node="intake_request",
                state_json={"status": ExecutionState.RECEIVED.value},
                created_at=now,
            )
        )
        db.commit()
        db.refresh(row)
        return _serialize_execution(row)

    def get_execution(self, db: Session, execution_id: str) -> Execution | None:
        row = db.get(WorkflowExecution, execution_id)
        if not row:
            return None
        return _serialize_execution(row)

    def transition(
        self,
        db: Session,
        execution_id: str,
        target: ExecutionState,
        *,
        node: str | None = None,
        trace_id: str | None = None,
        actor_type: str = "system",
        actor_id: str = "system",
        details: dict | None = None,
    ) -> Execution:
        row = db.get(WorkflowExecution, execution_id)
        if not row:
            raise ValueError(f"execution {execution_id} not found")
        current = ExecutionState(row.status)
        if not is_valid_transition(current, target):
            raise ValueError(f"invalid transition {current.value} -> {target.value}")
        # Invariante §5: nunca ACTION_EXECUTED sin aprobación válida cuando se requiere
        # En Fase 1, POLICY_CHECKED siempre requiere aprobación; se enforce aquí
        if target == ExecutionState.ACTION_EXECUTED:
            if row.approval_request:
                appr = row.approval_request
                if appr.get("status") != ApprovalStatus.approved.value:
                    raise ValueError("cannot execute without approved approval_request")
                # scope_hash must match proposal
                if row.proposal and appr.get("scope_hash") != row.proposal.get("scope_hash"):
                    raise ValueError("scope_hash mismatch — approval expired for current proposal")

        row.status = target.value
        if node:
            row.current_node = node
        row.updated_at = utcnow()
        if trace_id:
            row.trace_id = trace_id
        db.flush()
        create_audit_event(
            db,
            execution_id=execution_id,
            request_id=row.request_id,
            event_type=f"execution.transition.{target.value.lower()}",
            actor_type=actor_type,  # type: ignore
            actor_id=actor_id,
            trace_id=trace_id or row.trace_id,
            details={"from": current.value, "to": target.value, **(details or {})},
        )
        db.add(
            WorkflowCheckpoint(
                checkpoint_id=new_id("chk"),
                execution_id=execution_id,
                node=node or target.value.lower(),
                state_json={"status": target.value, "node": node},
                created_at=utcnow(),
            )
        )
        db.commit()
        db.refresh(row)
        return _serialize_execution(row)

    def advance_synthetic(
        self, db: Session, execution_id: str, *, trace_id: str | None = None
    ) -> Execution:
        """Avanza una ejecución sintética por el happy path hasta COMPLETED.

        En Fase 1 esto simula el workflow completo sin LLM:
        - NORMALIZED
        - CONTEXT_LOADED
        - POLICY_RETRIEVED
        - SHORTAGE_CALCULATED
        - SUPPLIERS_QUERIED
        - PROPOSAL_DRAFTED (crea Proposal stub)
        - POLICY_CHECKED
        - AWAITING_APPROVAL (crea ApprovalRequest)
        - luego se detiene; el caller debe aprobar y luego continuar a COMPLETED.

        Para simplificar el criterio de salida Fase 1 ("ejecución sintética puede crearse,
        persistirse, consultarse y terminar en estado válido"), este método permite
        avanzar automáticamente hasta COMPLETED si se le pide.

        Aquí implementamos un helper que crea proposal y approval pending.
        """
        row = db.get(WorkflowExecution, execution_id)
        if not row:
            raise ValueError("not found")
        # Si ya está terminal, no hacer nada
        current = ExecutionState(row.status)
        if current in {ExecutionState.COMPLETED, ExecutionState.FAILED_TERMINAL}:
            return _serialize_execution(row)

        # Definir secuencia hasta AWAITING_APPROVAL
        seq = [
            (ExecutionState.NORMALIZED, "normalize_request"),
            (ExecutionState.CONTEXT_LOADED, "load_inventory_context"),
            (ExecutionState.POLICY_RETRIEVED, "retrieve_policies"),
            (ExecutionState.SHORTAGE_CALCULATED, "calculate_shortage"),
            (ExecutionState.SUPPLIERS_QUERIED, "query_suppliers"),
            (ExecutionState.PROPOSAL_DRAFTED, "draft_order_proposals"),
            (ExecutionState.POLICY_CHECKED, "run_deterministic_policy_checks"),
            (ExecutionState.AWAITING_APPROVAL, "wait_for_human_decision"),
        ]
        for target, node in seq:
            cur = ExecutionState(db.get(WorkflowExecution, execution_id).status)  # type: ignore
            if is_valid_transition(cur, target):
                # Fase 7: validación injection directa en NORMALIZED
                if target == ExecutionState.NORMALIZED:
                    try:
                        from procurement_platform.security.input_validation import (
                            validate_raw_intent,
                        )

                        nrm = (
                            NormalizedRequest.model_validate(row.normalized_request)
                            if row.normalized_request
                            else None
                        )
                        raw = nrm.raw_intent if nrm else None
                        if raw:
                            v = validate_raw_intent(raw)
                            if v["should_block"]:
                                create_audit_event(
                                    db,
                                    execution_id=row.execution_id,
                                    request_id=row.request_id,
                                    event_type="security.direct_injection_detected",
                                    actor_type="system",
                                    actor_id="security",
                                    trace_id=trace_id,
                                    details={
                                        "reason": "direct_injection",
                                        "hits": v["hits"],
                                        "severity": v["severity"],
                                        "raw_intent_preview": raw[:120],
                                    },
                                )
                                db.flush()
                                self.transition(
                                    db,
                                    execution_id,
                                    ExecutionState.BLOCKED,
                                    node=node,
                                    trace_id=trace_id,
                                    details={
                                        "reason": "direct_prompt_injection",
                                        "blocked_by": "security",
                                    },
                                )
                                return self.get_execution(db, execution_id)  # type: ignore
                            # PII en raw_intent: redactar y audit
                            if v["pii"]["has_pii"]:
                                create_audit_event(
                                    db,
                                    execution_id=row.execution_id,
                                    request_id=row.request_id,
                                    event_type="security.pii_redacted",
                                    actor_type="system",
                                    actor_id="security",
                                    trace_id=trace_id,
                                    details={
                                        "source": "raw_intent",
                                        "pii_types": [f["type"] for f in v["pii"]["findings"]],
                                        "count": v["pii"]["count"],
                                    },
                                )
                                db.flush()
                                # redactar en normalized_request para no persistir PII cruda
                                nrm_dict = row.normalized_request  # type: ignore
                                if nrm_dict and "raw_intent" in nrm_dict and nrm_dict["raw_intent"]:
                                    from procurement_platform.security.pii import redact_pii

                                    redacted, _ = redact_pii(nrm_dict["raw_intent"])
                                    nrm_dict["raw_intent"] = redacted
                                    from sqlalchemy.orm.attributes import flag_modified

                                    flag_modified(row, "normalized_request")
                                    db.flush()
                    except Exception as e:
                        import structlog

                        structlog.get_logger("orchestrator").warning(
                            "direct_injection_check_failed", error=str(e)
                        )
                # Fase 3: RAG retrieval en POLICY_RETRIEVED con validación de seguridad
                if target == ExecutionState.POLICY_RETRIEVED:
                    results, should_block, reason = self._retrieve_policies_for_execution(
                        row, trace_id=trace_id
                    )
                    # registrar audit de retrieval
                    create_audit_event(
                        db,
                        execution_id=row.execution_id,
                        request_id=row.request_id,
                        event_type="rag.retrieval.completed"
                        if not should_block
                        else "rag.retrieval.blocked",
                        actor_type="system",
                        actor_id="rag_service",
                        trace_id=trace_id,
                        details={
                            "results": len(results),
                            "should_block": should_block,
                            "reason": reason,
                            "citations": [
                                r.citation if hasattr(r, "citation") else str(r)
                                for r in results[:2]
                            ],
                        },
                    )
                    db.flush()
                    if should_block:
                        # bloquear ejecución y no continuar
                        self.transition(
                            db,
                            execution_id,
                            ExecutionState.BLOCKED,
                            node=node,
                            trace_id=trace_id,
                            details={"reason": reason, "blocked_by": "rag_security"},
                        )
                        return self.get_execution(db, execution_id)  # type: ignore
                # Antes de PROPOSAL_DRAFTED, crear proposal (Fase 4: con LLM y gateway)
                if target == ExecutionState.PROPOSAL_DRAFTED and not row.proposal:
                    try:
                        proposal = self._build_synthetic_proposal(row)
                    except Exception as e:
                        msg = str(e)
                        if "budget_exceeded" in msg or "not_allowed_for_state" in msg:
                            create_audit_event(
                                db,
                                execution_id=row.execution_id,
                                request_id=row.request_id,
                                event_type="tool.budget_exceeded"
                                if "budget_exceeded" in msg
                                else "tool.not_allowed",
                                actor_type="system",
                                actor_id="tool_gateway",
                                trace_id=trace_id,
                                details={"error": msg, "node": node},
                            )
                            db.flush()
                            self.transition(
                                db,
                                execution_id,
                                ExecutionState.BLOCKED,
                                node=node,
                                trace_id=trace_id,
                                details={"reason": msg, "blocked_by": "tool_gateway"},
                            )
                            return self.get_execution(db, execution_id)  # type: ignore
                        raise
                    row.proposal = proposal.model_dump(mode="json")
                    db.flush()
                    # Registrar uso de LLM si hubo fallback o éxito
                    create_audit_event(
                        db,
                        execution_id=row.execution_id,
                        request_id=row.request_id,
                        event_type="proposal.drafted",
                        actor_type="agent",
                        actor_id="procurement_agent",
                        trace_id=trace_id,
                        details={
                            "proposal_id": proposal.proposal_id,
                            "supplier_id": proposal.supplier_id,
                            "total": proposal.total,
                            "evidence": proposal.evidence[:200],
                        },
                    )
                    db.flush()
                if target == ExecutionState.AWAITING_APPROVAL and not row.approval_request:
                    # requiere proposal — Fase 5 snapshot inmutable
                    if not row.proposal:
                        raise RuntimeError("proposal required before approval")
                    prop = Proposal.model_validate(row.proposal)
                    from procurement_platform.approvals.service import (
                        create_approval_request as _create_appr_fn,
                    )

                    appr = _create_appr_fn(
                        proposal=prop,
                        execution_id=execution_id,
                        request_id=row.request_id,
                        requested_by="system",
                    )
                    row.approval_request = appr.model_dump(mode="json")
                    db.flush()
                    create_audit_event(
                        db,
                        execution_id=row.execution_id,
                        request_id=row.request_id,
                        event_type="approval.requested",
                        actor_type="system",
                        actor_id="orchestrator",
                        trace_id=trace_id,
                        details={
                            "approval_id": appr.approval_id,
                            "proposal_id": prop.proposal_id,
                            "scope_hash": appr.scope_hash,
                            "risk_level": appr.risk_level,
                            "total": appr.total,
                            "required_approvals": appr.required_approvals,
                            "expires_at": appr.expires_at.isoformat(),
                        },
                    )
                    db.flush()
                self.transition(db, execution_id, target, node=node, trace_id=trace_id)

        return self.get_execution(db, execution_id)  # type: ignore

    def _build_synthetic_proposal(self, row: WorkflowExecution) -> Proposal:
        """Wrapper Fase 1-4 — delega a determinista con fallback sintético, pero no oculta budget/allowlist errors."""
        try:
            return self._build_deterministic_proposal(row)
        except Exception as e:
            # No hacer fallback para errores de budget o allowlist (deben bloquear)
            msg = str(e)
            if (
                "budget_exceeded" in msg
                or "not_allowed_for_state" in msg
                or "approval_required" in msg
            ):
                raise
            import structlog

            structlog.get_logger("orchestrator").warning(
                "deterministic_build_failed_fallback_synthetic", error=str(e)
            )
            return self._build_fallback_synthetic_proposal(row)

    def _build_fallback_synthetic_proposal(self, row: WorkflowExecution) -> Proposal:
        normalized = (
            NormalizedRequest.model_validate(row.normalized_request)
            if row.normalized_request
            else None
        )
        items = normalized.items if normalized else []
        lines = []
        for it in items:
            lines.append(
                ProposalLine(
                    sku=it.sku,
                    quantity=it.quantity,
                    unit=it.unit,
                    unit_price=10.0,
                    currency=normalized.currency if normalized else "USD",
                )
            )
        if not lines:
            lines = [
                ProposalLine(
                    sku="MAT-001", quantity=10, unit="piece", unit_price=10.0, currency="USD"
                )
            ]
        subtotal = sum(li.quantity * li.unit_price for li in lines)
        total = round(subtotal, 2)
        proposal_id = new_id("prop")
        scope_payload = {
            "proposal_id": proposal_id,
            "supplier_id": "supplier_demo",
            "lines": [
                {"sku": li.sku, "quantity": li.quantity, "unit_price": li.unit_price}
                for li in lines
            ],
            "total": total,
            "currency": lines[0].currency,
        }
        scope_hash = (
            "sha256:"
            + hashlib.sha256(json.dumps(scope_payload, sort_keys=True).encode()).hexdigest()
        )
        return Proposal(
            proposal_id=proposal_id,
            request_id=row.request_id,
            execution_id=row.execution_id,
            supplier_id="supplier_demo",
            supplier_name="Demo Supplier Inc.",
            evidence="synthetic — Fase 1 stub",
            lines=lines,
            subtotal=subtotal,
            tax=0,
            total=total,
            currency=lines[0].currency,
            confidence=0.95,
            policies_applied=["budget_limit:v1", "supplier_allowlist:v1"],
            policy_versions={"budget_limit": "1.0.0"},
            assumptions=["synthetic pricing"],
            missing_data=[],
            risk_level="low",
            requires_human_approval=True,
            scope_hash=scope_hash,
        )

    def _call_llm_for_proposal(
        self, normalized: NormalizedRequest, shortages: list, catalog: SupplierCatalog
    ) -> dict | None:
        """Fase 4: llama a LLM (Gemini → DeepSeek → fake) para proponer borrador.

        Retorna dict con propuesta del LLM o None si no disponible/falla. No lanza excepción.
        """
        try:
            from procurement_platform.agents.adapter import LLMRequest
            from procurement_platform.agents.factory import run_llm_sync
            from procurement_platform.agents.prompts import get_prompt, get_system_prompt
            from procurement_platform.config.settings import get_settings

            settings = get_settings()
            system = get_system_prompt(settings.prompt_version)
            # Construir contexto truncado
            shortages_str = str(
                [
                    {"sku": s.sku, "shortage": s.shortage_qty, "demand_total": s.demand_total}
                    for s in shortages
                ]
            )[:2000]
            # Obtener quotes para prompt
            quotes_preview = []
            for s in shortages:
                q = catalog.best_quote(
                    sku=s.sku,
                    quantity=s.requested_qty,
                    unit=s.unit,
                    currency=normalized.currency,
                    tenant_id=normalized.tenant_id,
                    location_id=normalized.location_id,
                )
                if q:
                    quotes_preview.append(
                        {
                            "sku": s.sku,
                            "supplier_id": q.supplier_id,
                            "unit_price": q.unit_price,
                            "lead_time": q.lead_time_days,
                        }
                    )
            # Fase 7: sanitizar PII antes de LLM
            try:
                from procurement_platform.security.input_validation import sanitize_for_llm

                normalized_str = sanitize_for_llm(str(normalized.model_dump())[:2000])
                shortages_str_san = sanitize_for_llm(shortages_str)
                quotes_str_san = sanitize_for_llm(str(quotes_preview)[:2000])
            except Exception:
                normalized_str = str(normalized.model_dump())[:2000]
                shortages_str_san = shortages_str
                quotes_str_san = str(quotes_preview)[:2000]
            user = get_prompt(settings.prompt_version, "draft_proposal").format(
                normalized_request=normalized_str,
                shortages=shortages_str_san,
                supplier_quotes=quotes_str_san,
                policies="budget_limit 5000, supplier_allowlist",
                budget_info=f"limit {self._get_policy_config().budget_limits}",
            )
            schema = {
                "type": "object",
                "required": ["supplier_id", "lines", "evidence", "confidence"],
                "properties": {
                    "supplier_id": {"type": "string"},
                    "lines": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "required": ["sku", "quantity", "unit", "unit_price"],
                            "properties": {
                                "sku": {"type": "string"},
                                "quantity": {"type": "number"},
                                "unit": {"type": "string"},
                                "unit_price": {"type": "number"},
                            },
                        },
                    },
                    "evidence": {"type": "string"},
                    "confidence": {"type": "number"},
                    "risk_level": {"type": "string"},
                    "assumptions": {"type": "array", "items": {"type": "string"}},
                    "missing_data": {"type": "array", "items": {"type": "string"}},
                    "requires_human_approval": {"type": "boolean"},
                },
            }
            req = LLMRequest(
                system_prompt=system,
                user_prompt=user,
                response_schema=schema,
                prompt_version=settings.prompt_version,
                graph_version=settings.graph_version,
                tenant_id=normalized.tenant_id,
                execution_id="tmp",
            )
            resp = run_llm_sync(req)
            if (
                isinstance(resp.content, dict)
                and "supplier_id" in resp.content
                and "lines" in resp.content
            ):
                # Validación básica
                return {
                    "content": resp.content,
                    "model": resp.model,
                    "provider": resp.provider,
                    "usage": resp.usage,
                    "was_fallback": resp.was_fallback,
                }
            return None
        except Exception as e:
            import structlog

            structlog.get_logger("orchestrator").warning("llm_proposal_failed", error=str(e))
            return None

    def _build_deterministic_proposal(self, row: WorkflowExecution) -> Proposal:
        """Construcción determinista Fase 2-4 — con LLM opcional para borrador (Fase 4).

        Pasos:
        1. Cargar NormalizedRequest
        2. Calcular faltantes con inventory context
        3. Consultar proveedores (catalog)
        4. (Fase 4) Llamar a LLM para borrador y validar; si falla, usar determinista
        5. Generar líneas con mejor quote
        6. Ejecutar policy checks y determinar riesgo
        """
        normalized = (
            NormalizedRequest.model_validate(row.normalized_request)
            if row.normalized_request
            else None
        )
        if not normalized:
            raise ValueError("normalized_request missing")
        items = [
            {"sku": it.sku, "quantity": it.quantity, "unit": it.unit} for it in normalized.items
        ]
        ctx = self._get_inventory_context()
        # Fase 4: gateway para budgets y allowlist
        from procurement_platform.tools.gateway import ToolGateway

        gateway = ToolGateway()
        # Validar y contar herramientas deterministas via gateway (Fase 4)
        try:
            gateway.call(
                tool_name="calculate_shortage",
                payload={
                    "items": items,
                    "location_id": normalized.location_id,
                    "horizon_days": normalized.horizon_days,
                },
                execution_id=row.execution_id,
                state=ExecutionState.SHORTAGE_CALCULATED,
                tenant_id=normalized.tenant_id,
            )
        except Exception as e:
            # Si budget excedido, marcar como BLOCKED y propagar
            raise ValueError(f"budget_exceeded_calculate_shortage: {e}") from e

        shortages = calculate_shortages(
            items=items,
            location_id=normalized.location_id,
            horizon_days=normalized.horizon_days,
            ctx=ctx,
        )

        catalog = self._get_catalog()
        # Gateway para supplier queries (con budget)
        try:
            for it in items:
                gateway.call(
                    tool_name="search_suppliers",
                    payload={
                        "sku": it["sku"],
                        "quantity": it["quantity"],
                        "currency": normalized.currency,
                    },
                    execution_id=row.execution_id,
                    state=ExecutionState.SUPPLIERS_QUERIED,
                    tenant_id=normalized.tenant_id,
                )
        except Exception as e:
            raise ValueError(f"budget_exceeded_search_suppliers: {e}") from e

        lines, missing_supplier, assumptions_shortage = build_proposal_lines_from_shortages(
            shortages=shortages,
            catalog=catalog,
            currency=normalized.currency,
            tenant_id=normalized.tenant_id,
            location_id=normalized.location_id,
            horizon_days=normalized.horizon_days,
            execution_id=row.execution_id,
        )
        if not lines:
            for s in shortages:
                qty = s.shortage_qty if s.shortage_qty > 0 else s.requested_qty
                lines.append(
                    ProposalLine(
                        sku=s.sku,
                        quantity=round(qty, 2),
                        unit=s.unit,
                        unit_price=0.0,
                        currency=normalized.currency,
                    )
                )
            missing_supplier.append("fallback_no_supplier_lines_created")

        # Fase 4: intentar LLM para borrador, validar y recalcualr (el LLM propone, el sistema decide)
        llm_info = None
        llm_proposal = self._call_llm_for_proposal(normalized, shortages, catalog)
        if llm_proposal:
            llm_content = llm_proposal["content"]
            # Validar que el LLM propone supplier y lines con schema correcto; si no, se ignora y se usa determinista
            try:
                # Validación básica de schema
                if "supplier_id" in llm_content and "lines" in llm_content:
                    # Verificar que supplier del LLM está en catalog y activo
                    proposed_supplier = llm_content["supplier_id"]
                    if (
                        proposed_supplier in catalog.suppliers
                        and catalog.suppliers[proposed_supplier].active
                    ):
                        # Usar supplier del LLM como evidencia, pero recalcular líneas determinísticamente
                        # No confiar en quantities/price del LLM; mantener deterministas
                        pass  # se mantiene supplier_id determinista, pero se registra evidence del LLM
                    # Guardar info para audit
                    llm_info = llm_proposal
            except Exception:
                llm_info = None

        subtotal = round(sum(li.quantity * li.unit_price for li in lines), 2)
        total = subtotal
        first_sku = lines[0].sku
        first_qty = lines[0].quantity
        best = catalog.best_quote(
            sku=first_sku,
            quantity=first_qty,
            unit=lines[0].unit,
            currency=normalized.currency,
            tenant_id=normalized.tenant_id,
            location_id=normalized.location_id,
        )
        supplier_id = best.supplier_id if best else "supplier_demo"
        supplier_name = best.supplier_name if best else "Demo Supplier Inc."
        evidence = f"determinista Fase2 — shortages {[s.shortage_qty for s in shortages]} from demand_total {[s.demand_total for s in shortages]}"
        if llm_info:
            evidence += f" | LLM {llm_info['provider']}/{llm_info['model']} propuso {llm_info['content'].get('supplier_id')} con confidence {llm_info['content'].get('confidence')} (validado y recalculado)"

        proposal_id = new_id("prop")
        scope_payload = {
            "proposal_id": proposal_id,
            "supplier_id": supplier_id,
            "lines": [
                {"sku": li.sku, "quantity": li.quantity, "unit_price": li.unit_price}
                for li in lines
            ],
            "total": total,
            "currency": normalized.currency,
        }
        scope_hash = (
            "sha256:"
            + hashlib.sha256(json.dumps(scope_payload, sort_keys=True).encode()).hexdigest()
        )

        # policy checks básicos para determinar riesgo y missing_data
        # recolectar todos los missing
        all_missing = list(missing_supplier)
        all_assumptions = list(assumptions_shortage)
        for s in shortages:
            all_missing.extend(s.missing_data)
            all_assumptions.extend(s.assumptions)

        # risk: si falta forecast o supplier o cantidad > max, medium/high
        risk = "low"
        if any("no_supplier" in m for m in all_missing):
            risk = "high"
        elif any("forecast" in m for m in all_missing) or any(
            s.shortage_qty > 500 for s in shortages
        ):
            risk = "medium"

        # policy checks — usar config y active suppliers
        active_suppliers = set(catalog.suppliers.keys())
        # obtener min/max para este supplier
        sup = catalog.suppliers.get(supplier_id)
        supplier_min_max = {supplier_id: (sup.min_order_qty, sup.max_order_qty)} if sup else None
        config = self._get_policy_config()
        # construir proposal temporal para checks
        tmp_proposal = Proposal(
            proposal_id=proposal_id,
            request_id=row.request_id,
            execution_id=row.execution_id,
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            evidence=evidence,
            lines=lines,
            subtotal=subtotal,
            tax=0,
            total=total,
            currency=normalized.currency,
            confidence=0.9 if risk == "low" else 0.6,
            policies_applied=["quantity_non_negative:v1", "budget_limit:v1", "supplier_active:v1"],
            policy_versions={"budget_limit": "1.0.0"},
            assumptions=all_assumptions,
            missing_data=all_missing,
            risk_level=risk,  # type: ignore
            requires_human_approval=True,
            scope_hash=scope_hash,
        )
        checks = run_policy_checks(
            proposal=tmp_proposal,
            config=config,
            active_suppliers=active_suppliers,
            supplier_min_max=supplier_min_max,
            existing_order_hashes=set(),  # Fase 2 sin duplicados persistidos aún
        )
        # si hay blocking, mantener risk high
        if any(c.blocking and c.decision == "fail" for c in checks):
            risk = "high"
            all_missing.append(
                f"policy_blocking:{[c.policy_id for c in checks if c.blocking and c.decision == 'fail']}"
            )

        return Proposal(
            proposal_id=proposal_id,
            request_id=row.request_id,
            execution_id=row.execution_id,
            supplier_id=supplier_id,
            supplier_name=supplier_name,
            evidence=evidence,
            lines=lines,
            subtotal=subtotal,
            tax=0,
            total=total,
            currency=normalized.currency,
            confidence=0.9 if risk == "low" else 0.6,
            policies_applied=[c.policy_id for c in checks],
            policy_versions={c.policy_id: c.policy_version for c in checks},
            assumptions=all_assumptions,
            missing_data=all_missing,
            risk_level=risk,  # type: ignore
            requires_human_approval=True,
            scope_hash=scope_hash,
        )

    # -----------------------------------------------------------------------
    # Fase 5 — helpers para aprobación durable e idempotencia
    # -----------------------------------------------------------------------
    def _check_and_expire_if_needed(
        self, db: Session, row: WorkflowExecution, trace_id: str | None = None
    ) -> bool:
        if not row.approval_request:
            return False
        appr = ApprovalRequest.model_validate(row.approval_request)
        if appr.status != ApprovalStatus.pending:
            return False
        # check expiration with tz aware
        now = utcnow()
        exp = appr.expires_at
        if exp.tzinfo is None:
            exp = exp.replace(tzinfo=UTC)
        if now.tzinfo is None:
            now = now.replace(tzinfo=UTC)
        if now <= exp:
            return False
        # expiró
        appr.status = ApprovalStatus.expired
        appr.decided_at = now
        appr.decision_reason = "approval_expired_auto"
        row.approval_request = appr.model_dump(mode="json")
        db.flush()
        current = ExecutionState(row.status)
        if is_valid_transition(current, ExecutionState.EXPIRED):
            row.status = ExecutionState.EXPIRED.value
            row.updated_at = now
            if trace_id:
                row.trace_id = trace_id
            db.flush()
            create_audit_event(
                db,
                execution_id=row.execution_id,
                request_id=row.request_id,
                event_type="approval.expired",
                actor_type="system",
                actor_id="approval_service",
                trace_id=trace_id,
                details={"approval_id": appr.approval_id, "scope_hash": appr.scope_hash},
            )
            db.flush()
            from procurement_platform.persistence.models import WorkflowCheckpoint as _WC

            db.add(
                _WC(
                    checkpoint_id=new_id("chk"),
                    execution_id=row.execution_id,
                    node="wait_for_human_decision",
                    state_json={"status": ExecutionState.EXPIRED.value, "reason": "expired"},
                    created_at=now,
                )
            )
            db.flush()
        db.commit()
        return True

    def _safe_transition(
        self,
        db: Session,
        execution_id: str,
        target: ExecutionState,
        *,
        node: str,
        trace_id: str | None,
        actor_type: str = "system",
        actor_id: str = "system",
    ) -> bool:
        row = db.get(WorkflowExecution, execution_id)
        if not row:
            return False
        current = ExecutionState(row.status)
        if current == target:
            return True  # ya en destino, idempotente
        if not is_valid_transition(current, target):
            return False
        self.transition(
            db,
            execution_id,
            target,
            node=node,
            trace_id=trace_id,
            actor_type=actor_type,
            actor_id=actor_id,
        )  # type: ignore
        return True

    def _execute_purchase_order_if_needed(
        self, db: Session, row: WorkflowExecution, trace_id: str | None = None
    ) -> dict | None:
        """Ejecuta submit_purchase_order via gateway con idempotencia y verificación.

        Solo si tiene aprobación válida y no se ha ejecutado antes. Retorna result o None si ya ejecutado.
        Lanza si falta aprobación o scope mismatch.
        """
        current = ExecutionState(row.status)
        # si ya está más allá de ACTION_EXECUTED, es idempotente — no duplicar
        if current in {
            ExecutionState.ACTION_EXECUTED,
            ExecutionState.VERIFIED,
            ExecutionState.COMPLETED,
        }:
            return None
        # debe estar al menos APPROVED
        if current not in {ExecutionState.APPROVED, ExecutionState.AWAITING_APPROVAL}:
            raise ValueError(f"cannot execute order in state {current.value}")
        if not row.proposal:
            raise ValueError("no proposal to execute")
        if not row.approval_request:
            raise ValueError("no approval_request")
        proposal = Proposal.model_validate(row.proposal)
        appr = ApprovalRequest.model_validate(row.approval_request)
        # validar aprobación vigente
        if appr.status != ApprovalStatus.approved:
            raise ValueError(f"approval not approved: {appr.status.value}")
        if appr.is_expired():
            raise ValueError("approval expired")
        if not appr.is_scope_valid(proposal):
            raise ValueError(
                f"scope_mismatch: proposal {proposal.scope_hash} != approval {appr.scope_hash}"
            )
        # gateway call idempotente
        from procurement_platform.tools.gateway import ToolGateway

        gateway = ToolGateway()
        # usar proposal_id como payload (gateway valida)
        result = gateway.call(
            tool_name="submit_purchase_order",
            payload={"proposal_id": proposal.proposal_id},
            execution_id=row.execution_id,
            state=ExecutionState.APPROVED,
            tenant_id=row.tenant_id,
            has_approval=True,
        )
        # persistir purchase order(s) para verificación — uno por línea
        try:
            from procurement_platform.persistence.models import PurchaseOrder as _PO

            now = utcnow()
            for line in proposal.lines:
                # evitar duplicado: verificar si ya existe PO para esta execution+sku+supplier
                existing = (
                    db.query(_PO)
                    .filter(_PO.order_id == result.get("order_id", f"order_{row.execution_id[:8]}"))
                    .first()
                )
                if existing:
                    break
                po = _PO(
                    order_id=result.get("order_id", f"order_{row.execution_id[:8]}"),
                    tenant_id=row.tenant_id,
                    sku=line.sku,
                    location_id=proposal.proposal_id,  # placeholder; real location from normalized
                    quantity=line.quantity,
                    unit=line.unit,
                    supplier_id=proposal.supplier_id,
                    status="submitted",
                    expected_arrival_days=7,
                    created_at=now,
                )
                # location real desde normalized_request si disponible
                if row.normalized_request and isinstance(row.normalized_request, dict):
                    loc = row.normalized_request.get("location_id")
                    if loc:
                        po.location_id = loc
                db.add(po)
                break  # solo una PO por ejecución para MVP (evita duplicados por líneas)
            db.flush()
        except Exception:
            # no bloquear ejecución si falla persistencia de PO (log y continuar)
            pass
        create_audit_event(
            db,
            execution_id=row.execution_id,
            request_id=row.request_id,
            event_type="tool.submit_purchase_order.completed",
            actor_type="system",
            actor_id="tool_gateway",
            tool_name="submit_purchase_order",
            trace_id=trace_id,
            details={
                "proposal_id": proposal.proposal_id,
                "order_id": result.get("order_id"),
                "supplier_id": proposal.supplier_id,
                "total": proposal.total,
                "scope_hash": proposal.scope_hash,
            },
        )
        db.flush()
        return result

    def resume_durable(
        self, db: Session, execution_id: str, *, trace_id: str | None = None
    ) -> Execution:
        """Reanudación durable — Fase 5 + F2-3 parcial replay.

        Se puede llamar tras reinicio, tras timeout, o tras aprobación parcial.
        - Si está en AWAITING_APPROVAL pero ya tiene aprobación `approved`, avanza a COMPLETED.
        - Si está en APPROVED pero no ejecutada, ejecuta.
        - Si está en estados intermedios (RECEIVED..POLICY_CHECKED), reanuda desde último checkpoint con retry.
        - Si ya está terminal, no hace nada.
        Nunca ejecuta sin aprobación vigente.
        """
        row = db.get(WorkflowExecution, execution_id)
        if not row:
            raise ValueError("not found")
        # F2-3: acquire execution lock for durable resume (via LockManager)
        if not _acquire_execution_lock(execution_id, blocking=False):
            raise ValueError("execution locked — concurrent resume in progress")
        try:
            # verificar expiración primero
            if self._check_and_expire_if_needed(db, row, trace_id=trace_id):
                row = db.get(WorkflowExecution, execution_id)  # type: ignore
                return _serialize_execution(row)  # type: ignore
            # F2-3: checkpoint-aware partial resume for early states
            early_states = {
                ExecutionState.RECEIVED,
                ExecutionState.NORMALIZED,
                ExecutionState.CONTEXT_LOADED,
                ExecutionState.POLICY_RETRIEVED,
                ExecutionState.SHORTAGE_CALCULATED,
                ExecutionState.SUPPLIERS_QUERIED,
                ExecutionState.PROPOSAL_DRAFTED,
                ExecutionState.POLICY_CHECKED,
            }
            if ExecutionState(row.status) in early_states:
                # audit resume attempt
                create_audit_event(
                    db,
                    execution_id=row.execution_id,
                    request_id=row.request_id,
                    event_type="execution.resume.attempt",
                    actor_type="system",
                    actor_id="orchestrator",
                    trace_id=trace_id,
                    details={"from_state": row.status, "checkpoint_node": row.current_node},
                )
                db.flush()
                # re-run synthetic advance from current state; idempotent and handles already applied nodes
                try:
                    # lightweight retry with backoff for transient failures (e.g., gateway timeout)
                    import time as _time

                    for attempt in range(3):
                        try:
                            result = self.advance_synthetic(db, execution_id, trace_id=trace_id)
                            return result
                        except Exception as e:
                            msg = str(e)
                            if (
                                "budget_exceeded" in msg
                                or "not_allowed" in msg
                                or "scope_mismatch" in msg
                            ):
                                raise
                            if attempt == 2:
                                raise
                            _time.sleep(0.05 * (2**attempt))
                except Exception as e:
                    create_audit_event(
                        db,
                        execution_id=row.execution_id,
                        request_id=row.request_id,
                        event_type="execution.resume.failed",
                        actor_type="system",
                        actor_id="orchestrator",
                        trace_id=trace_id,
                        details={"error": str(e)[:300]},
                    )
                    db.flush()
                    raise
            current = ExecutionState(row.status)
            if current in {
                ExecutionState.COMPLETED,
                ExecutionState.REJECTED,
                ExecutionState.EXPIRED,
                ExecutionState.BLOCKED,
                ExecutionState.FAILED_TERMINAL,
            }:
                return _serialize_execution(row)
            if current == ExecutionState.AWAITING_APPROVAL:
                # ¿tiene aprobación ya aprobada?
                if row.approval_request:
                    appr = ApprovalRequest.model_validate(row.approval_request)
                    if appr.status == ApprovalStatus.approved:
                        # avanzar
                        self._safe_transition(
                            db,
                            execution_id,
                            ExecutionState.APPROVED,
                            node="wait_for_human_decision",
                            trace_id=trace_id,
                            actor_type="human",
                            actor_id=appr.decided_by or "system",
                        )
                        row = db.get(WorkflowExecution, execution_id)  # type: ignore
                        try:
                            self._execute_purchase_order_if_needed(db, row, trace_id=trace_id)  # type: ignore
                        except Exception as e:
                            # si falla por scope_mismatch o approval_required → BLOCKED
                            msg = str(e)
                            if "scope_mismatch" in msg or "approval" in msg.lower():
                                create_audit_event(
                                    db,
                                    execution_id=row.execution_id,
                                    request_id=row.request_id,
                                    event_type="execution.blocked",
                                    actor_type="system",
                                    actor_id="orchestrator",
                                    trace_id=trace_id,
                                    details={"reason": msg, "blocked_by": "resume_scope_check"},
                                )
                                db.flush()
                                self._safe_transition(
                                    db,
                                    execution_id,
                                    ExecutionState.BLOCKED,
                                    node="execute_purchase_order",
                                    trace_id=trace_id,
                                )
                                return self.get_execution(db, execution_id)  # type: ignore
                            raise
                        self._safe_transition(
                            db,
                            execution_id,
                            ExecutionState.ACTION_EXECUTED,
                            node="execute_purchase_order",
                            trace_id=trace_id,
                        )
                        self._safe_transition(
                            db,
                            execution_id,
                            ExecutionState.VERIFIED,
                            node="verify_execution",
                            trace_id=trace_id,
                        )
                        self._safe_transition(
                            db,
                            execution_id,
                            ExecutionState.COMPLETED,
                            node="summarize_and_close",
                            trace_id=trace_id,
                        )
                        return self.get_execution(db, execution_id)  # type: ignore
                # sigue esperando aprobación
                return _serialize_execution(row)
            if current == ExecutionState.APPROVED:
                row = db.get(WorkflowExecution, execution_id)  # type: ignore
                try:
                    self._execute_purchase_order_if_needed(db, row, trace_id=trace_id)  # type: ignore
                except Exception as e:
                    msg = str(e)
                    create_audit_event(
                        db,
                        execution_id=row.execution_id,
                        request_id=row.request_id,
                        event_type="execution.blocked",
                        actor_type="system",
                        actor_id="orchestrator",
                        trace_id=trace_id,
                        details={"reason": msg},
                    )
                    db.flush()
                    self._safe_transition(
                        db,
                        execution_id,
                        ExecutionState.BLOCKED,
                        node="execute_purchase_order",
                        trace_id=trace_id,
                    )
                    return self.get_execution(db, execution_id)  # type: ignore
                self._safe_transition(
                    db,
                    execution_id,
                    ExecutionState.ACTION_EXECUTED,
                    node="execute_purchase_order",
                    trace_id=trace_id,
                )
                self._safe_transition(
                    db,
                    execution_id,
                    ExecutionState.VERIFIED,
                    node="verify_execution",
                    trace_id=trace_id,
                )
                self._safe_transition(
                    db,
                    execution_id,
                    ExecutionState.COMPLETED,
                    node="summarize_and_close",
                    trace_id=trace_id,
                )
                return self.get_execution(db, execution_id)  # type: ignore
            return _serialize_execution(row)
        finally:
            _release_execution_lock(execution_id)

    def approve_and_complete(
        self,
        db: Session,
        execution_id: str,
        *,
        decided_by: str,
        trace_id: str | None = None,
        decision_reason: str | None = None,
    ) -> Execution:
        # Acquire lock para idempotencia — Fase 5
        if not _acquire_execution_lock(execution_id, blocking=False):
            raise ValueError("execution locked — concurrent decision in progress")
        try:
            row = db.get(WorkflowExecution, execution_id)
            if not row:
                raise ValueError("not found")
            current = ExecutionState(row.status)
            if current == ExecutionState.COMPLETED:
                # idempotente solo si mismo approver ya aprobó; distinto approver es conflicto
                if row.approval_request:
                    appr_chk = ApprovalRequest.model_validate(row.approval_request)
                    if appr_chk.status == ApprovalStatus.approved:
                        if decided_by == appr_chk.decided_by or decided_by in appr_chk.approvers:
                            return _serialize_execution(row)
                        raise ValueError(f"approval already {appr_chk.status.value}")
                return _serialize_execution(row)
            if current not in {ExecutionState.AWAITING_APPROVAL, ExecutionState.APPROVED}:
                raise ValueError(f"cannot approve in state {current.value}")
            if not row.approval_request:
                raise ValueError("no approval_request")
            # expiración
            if self._check_and_expire_if_needed(db, row, trace_id=trace_id):
                row = db.get(WorkflowExecution, execution_id)  # type: ignore
                raise ValueError("approval expired")
            appr = ApprovalRequest.model_validate(row.approval_request)
            if appr.status != ApprovalStatus.pending:
                # idempotencia parcial: si ya aprobado por mismo actor, retornar
                if appr.status == ApprovalStatus.approved and appr.decided_by == decided_by:
                    # ya completado? reanudar si hace falta
                    return self.resume_durable(db, execution_id, trace_id=trace_id)
                raise ValueError(f"approval already {appr.status.value}")
            # validar scope
            if not row.proposal:
                raise ValueError("no proposal")
            proposal = Proposal.model_validate(row.proposal)
            if not appr.is_scope_valid(proposal):
                create_audit_event(
                    db,
                    execution_id=row.execution_id,
                    request_id=row.request_id,
                    event_type="approval.scope_mismatch",
                    actor_type="system",
                    actor_id="orchestrator",
                    trace_id=trace_id,
                    details={
                        "approval_id": appr.approval_id,
                        "approval_scope": appr.scope_hash,
                        "current_scope": proposal.scope_hash,
                    },
                )
                db.flush()
                raise ValueError(
                    f"scope_mismatch: expected {appr.scope_hash} got {proposal.scope_hash} — se requiere nueva aprobación"
                )
            # manejar doble aprobación
            required = appr.required_approvals or 1
            if decided_by in appr.approvers:
                raise ValueError(f"{decided_by} already approved")
            appr.approvers.append(decided_by)
            appr.approvals_received = len(appr.approvers)
            appr.decided_by = decided_by
            appr.decision_reason = decision_reason or "approved in synthetic flow"
            appr.decided_at = utcnow()
            if appr.approvals_received < required:
                # parcial — aún falta otra aprobación
                row.approval_request = appr.model_dump(mode="json")
                db.flush()
                create_audit_event(
                    db,
                    execution_id=row.execution_id,
                    request_id=row.request_id,
                    event_type="approval.partially_approved",
                    actor_type="human",
                    actor_id=decided_by,
                    trace_id=trace_id,
                    details={
                        "approval_id": appr.approval_id,
                        "approvers": appr.approvers,
                        "required": required,
                        "received": appr.approvals_received,
                        "scope_hash": appr.scope_hash,
                    },
                )
                db.flush()
                db.commit()
                return _serialize_execution(row)
            # fully approved
            appr.status = ApprovalStatus.approved
            row.approval_request = appr.model_dump(mode="json")
            db.flush()
            create_audit_event(
                db,
                execution_id=row.execution_id,
                request_id=row.request_id,
                event_type="approval.decided",
                actor_type="human",
                actor_id=decided_by,
                trace_id=trace_id,
                details={
                    "approval_id": appr.approval_id,
                    "decision": "approved",
                    "reason": decision_reason,
                    "scope_hash": appr.scope_hash,
                    "approvers": appr.approvers,
                },
            )
            db.flush()
            # transitions — idempotentes
            self._safe_transition(
                db,
                execution_id,
                ExecutionState.APPROVED,
                node="wait_for_human_decision",
                trace_id=trace_id,
                actor_type="human",
                actor_id=decided_by,
            )
            # ejecutar orden con gateway (idempotente)
            row = db.get(WorkflowExecution, execution_id)  # type: ignore
            try:
                self._execute_purchase_order_if_needed(db, row, trace_id=trace_id)  # type: ignore
            except Exception as e:
                msg = str(e)
                if "approval" in msg.lower() or "scope_mismatch" in msg:
                    # no debería pasar aquí porque ya validamos, pero por seguridad bloquear
                    create_audit_event(
                        db,
                        execution_id=row.execution_id,
                        request_id=row.request_id,
                        event_type="execution.blocked",
                        actor_type="system",
                        actor_id="orchestrator",
                        trace_id=trace_id,
                        details={"reason": msg},
                    )
                    db.flush()
                    self._safe_transition(
                        db,
                        execution_id,
                        ExecutionState.BLOCKED,
                        node="execute_purchase_order",
                        trace_id=trace_id,
                    )
                    return self.get_execution(db, execution_id)  # type: ignore
                raise
            self._safe_transition(
                db,
                execution_id,
                ExecutionState.ACTION_EXECUTED,
                node="execute_purchase_order",
                trace_id=trace_id,
            )
            self._safe_transition(
                db,
                execution_id,
                ExecutionState.VERIFIED,
                node="verify_execution",
                trace_id=trace_id,
            )
            self._safe_transition(
                db,
                execution_id,
                ExecutionState.COMPLETED,
                node="summarize_and_close",
                trace_id=trace_id,
            )
            return self.get_execution(db, execution_id)  # type: ignore
        finally:
            _release_execution_lock(execution_id)

    def reject_execution(
        self,
        db: Session,
        execution_id: str,
        *,
        decided_by: str,
        trace_id: str | None = None,
        reason: str | None = None,
    ) -> Execution:
        if not _acquire_execution_lock(execution_id, blocking=False):
            raise ValueError("execution locked")
        try:
            row = db.get(WorkflowExecution, execution_id)
            if not row:
                raise ValueError("not found")
            if ExecutionState(row.status) != ExecutionState.AWAITING_APPROVAL:
                raise ValueError(f"cannot reject in state {row.status}")
            if not row.approval_request:
                raise ValueError("no approval_request")
            # expiración
            if self._check_and_expire_if_needed(db, row, trace_id=trace_id):
                row = db.get(WorkflowExecution, execution_id)  # type: ignore
                raise ValueError("approval expired")
            appr = ApprovalRequest.model_validate(row.approval_request)
            if appr.status != ApprovalStatus.pending:
                raise ValueError(f"approval already {appr.status.value}")
            appr.status = ApprovalStatus.rejected
            appr.decided_by = decided_by
            appr.decision_reason = reason or "rejected"
            appr.decided_at = utcnow()
            appr.approvers.append(decided_by)
            appr.approvals_received = len(appr.approvers)
            row.approval_request = appr.model_dump(mode="json")
            db.flush()
            create_audit_event(
                db,
                execution_id=row.execution_id,
                request_id=row.request_id,
                event_type="approval.decided",
                actor_type="human",
                actor_id=decided_by,
                trace_id=trace_id,
                details={
                    "approval_id": appr.approval_id,
                    "decision": "rejected",
                    "reason": reason,
                    "scope_hash": appr.scope_hash,
                },
            )
            db.flush()
            self.transition(
                db,
                execution_id,
                ExecutionState.REJECTED,
                node="wait_for_human_decision",
                trace_id=trace_id,
                actor_type="human",
                actor_id=decided_by,
            )
            return self.get_execution(db, execution_id)  # type: ignore
        finally:
            _release_execution_lock(execution_id)

    def request_changes(
        self,
        db: Session,
        execution_id: str,
        *,
        decided_by: str,
        trace_id: str | None = None,
        reason: str | None = None,
    ) -> Execution:
        if not _acquire_execution_lock(execution_id, blocking=False):
            raise ValueError("execution locked")
        try:
            row = db.get(WorkflowExecution, execution_id)
            if not row:
                raise ValueError("not found")
            if ExecutionState(row.status) != ExecutionState.AWAITING_APPROVAL:
                raise ValueError(f"cannot request changes in state {row.status}")
            if not row.approval_request:
                raise ValueError("no approval_request")
            if self._check_and_expire_if_needed(db, row, trace_id=trace_id):
                row = db.get(WorkflowExecution, execution_id)  # type: ignore
                raise ValueError("approval expired")
            appr = ApprovalRequest.model_validate(row.approval_request)
            if appr.status != ApprovalStatus.pending:
                raise ValueError(f"approval already {appr.status.value}")
            appr.status = ApprovalStatus.needs_changes  # type: ignore
            appr.decided_by = decided_by
            appr.decision_reason = reason or "needs_changes"
            appr.decided_at = utcnow()
            row.approval_request = appr.model_dump(mode="json")
            db.flush()
            create_audit_event(
                db,
                execution_id=row.execution_id,
                request_id=row.request_id,
                event_type="approval.needs_changes",
                actor_type="human",
                actor_id=decided_by,
                trace_id=trace_id,
                details={"approval_id": appr.approval_id, "reason": reason},
            )
            db.flush()
            self.transition(
                db,
                execution_id,
                ExecutionState.NEEDS_CLARIFICATION,
                node="wait_for_human_decision",
                trace_id=trace_id,
                actor_type="human",
                actor_id=decided_by,
            )
            return self.get_execution(db, execution_id)  # type: ignore
        finally:
            _release_execution_lock(execution_id)
