"""Workflow orchestrator — runtime propio (Fase 1-3).

Grafo lineal determinista:
RECEIVED -> NORMALIZED -> CONTEXT_LOADED -> ... -> COMPLETED
Fase 1: grafo sintético sin LLM.
Fase 2: cálculo determinista de faltantes, proveedores y policy checks — sin LLM.
Fase 3: RAG seguro con filtros, citas y bloqueo de contenido malicioso — sin LLM para decisiones.

Contratos §5 y §7. Cálculos críticos no llaman al modelo (Fase 2-3 criterio salida).
"""
from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
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
                "items": [{"sku": "MAT-001", "on_hand": 20, "reserved": 5, "in_transit": 10, "daily_demand_forecast": 8}],
                "lead_times_days": {"MAT-001": 7},
            }
        # open orders fixture (Fase 2 nuevo)
        open_orders_path = Path("evals/fixtures/open_orders.json")
        open_orders = []
        if open_orders_path.exists():
            open_orders = _json.loads(open_orders_path.read_text(encoding="utf-8")).get("open_orders", [])

        return load_context_from_fixtures(inventory_fixture=inv_fixture, open_orders_fixture=open_orders)
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
        {"suppliers": [{"supplier_id": "supplier_demo", "name": "Demo Supplier Inc.", "active": True, "allowed_tenants": ["tenant_demo"], "currency": "USD", "min_order": 1, "max_order": 10000, "lead_time_days": 7}], "quotes": [{"sku": "MAT-001", "unit_price": 10.0}]}
    )


def _load_default_policy_config() -> PolicyConfig:
    # budget_limits por defecto: tenant_demo/* = 5000 (ver fixtures/policies_budget_limit_v1.json)
    try:
        import json as _json

        pol_path = Path("evals/fixtures/policies_budget_limit_v1.json")
        if pol_path.exists():
            pol = _json.loads(pol_path.read_text(encoding="utf-8"))
            limit = pol.get("rules", {}).get("delegated_limit_usd", 5000)
            return PolicyConfig(budget_limits={("tenant_demo", "*"): float(limit), ("tenant_demo", "warehouse_north"): float(limit)})
    except Exception:
        pass
    return PolicyConfig(budget_limits={("tenant_demo", "*"): 5000.0, ("tenant_demo", "warehouse_north"): 5000.0})


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


class WorkflowOrchestrator:
    """Orquestador sincrónico — Fase 1-3 (sin LLM para cálculos críticos y RAG seguro)."""

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

    def _retrieve_policies_for_execution(self, row: WorkflowExecution, trace_id: str | None = None) -> tuple[list, bool, str]:
        """Fase 3: recupera políticas via RAG con filtros tenant/vigencia y valida seguridad.

        Retorna (results, should_block, reason) y registra audit.
        """
        rag = self._get_rag_service()
        if rag is None:
            return [], False, "rag_not_configured"
        normalized = NormalizedRequest.model_validate(row.normalized_request) if row.normalized_request else None
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

    def advance_synthetic(self, db: Session, execution_id: str, *, trace_id: str | None = None) -> Execution:
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
                # Fase 3: RAG retrieval en POLICY_RETRIEVED con validación de seguridad
                if target == ExecutionState.POLICY_RETRIEVED:
                    results, should_block, reason = self._retrieve_policies_for_execution(row, trace_id=trace_id)
                    # registrar audit de retrieval
                    create_audit_event(
                        db,
                        execution_id=row.execution_id,
                        request_id=row.request_id,
                        event_type="rag.retrieval.completed" if not should_block else "rag.retrieval.blocked",
                        actor_type="system",
                        actor_id="rag_service",
                        trace_id=trace_id,
                        details={"results": len(results), "should_block": should_block, "reason": reason, "citations": [r.citation if hasattr(r, "citation") else str(r) for r in results[:2]]},
                    )
                    db.flush()
                    if should_block:
                        # bloquear ejecución y no continuar
                        self.transition(db, execution_id, ExecutionState.BLOCKED, node=node, trace_id=trace_id, details={"reason": reason, "blocked_by": "rag_security"})
                        return self.get_execution(db, execution_id)  # type: ignore
                # Antes de PROPOSAL_DRAFTED, crear proposal
                if target == ExecutionState.PROPOSAL_DRAFTED and not row.proposal:
                    proposal = self._build_synthetic_proposal(row)
                    row.proposal = proposal.model_dump(mode="json")
                    db.flush()
                if target == ExecutionState.AWAITING_APPROVAL and not row.approval_request:
                    # requiere proposal
                    if not row.proposal:
                        raise RuntimeError("proposal required before approval")
                    prop = Proposal.model_validate(row.proposal)
                    appr = ApprovalRequest(
                        approval_id=new_id("appr"),
                        proposal_id=prop.proposal_id,
                        execution_id=execution_id,
                        request_id=row.request_id,
                        status=ApprovalStatus.pending,
                        scope_hash=prop.scope_hash,
                        requested_by="system",
                        requested_at=utcnow(),
                        expires_at=utcnow() + timedelta(hours=24),
                    )
                    row.approval_request = appr.model_dump(mode="json")
                    db.flush()
                self.transition(db, execution_id, target, node=node, trace_id=trace_id)

        return self.get_execution(db, execution_id)  # type: ignore

    def _build_synthetic_proposal(self, row: WorkflowExecution) -> Proposal:
        """Wrapper Fase 1 — ahora delega a determinista (Fase 2) con fallback sintético."""
        try:
            return self._build_deterministic_proposal(row)
        except Exception as e:
            # fallback sintético Fase 1 para no romper compatibilidad
            import structlog

            structlog.get_logger("orchestrator").warning("deterministic_build_failed_fallback_synthetic", error=str(e))
            return self._build_fallback_synthetic_proposal(row)

    def _build_fallback_synthetic_proposal(self, row: WorkflowExecution) -> Proposal:
        normalized = NormalizedRequest.model_validate(row.normalized_request) if row.normalized_request else None
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
            lines = [ProposalLine(sku="MAT-001", quantity=10, unit="piece", unit_price=10.0, currency="USD")]
        subtotal = sum(li.quantity * li.unit_price for li in lines)
        total = round(subtotal, 2)
        proposal_id = new_id("prop")
        scope_payload = {
            "proposal_id": proposal_id,
            "supplier_id": "supplier_demo",
            "lines": [{"sku": li.sku, "quantity": li.quantity, "unit_price": li.unit_price} for li in lines],
            "total": total,
            "currency": lines[0].currency,
        }
        scope_hash = "sha256:" + hashlib.sha256(json.dumps(scope_payload, sort_keys=True).encode()).hexdigest()
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

    def _build_deterministic_proposal(self, row: WorkflowExecution) -> Proposal:
        """Construcción determinista Fase 2 — sin LLM.

        Pasos:
        1. Cargar NormalizedRequest
        2. Calcular faltantes con inventory context
        3. Consultar proveedores (catalog)
        4. Generar líneas con mejor quote
        5. Ejecutar policy checks y determinar riesgo
        """
        normalized = NormalizedRequest.model_validate(row.normalized_request) if row.normalized_request else None
        if not normalized:
            raise ValueError("normalized_request missing")
        items = [{"sku": it.sku, "quantity": it.quantity, "unit": it.unit} for it in normalized.items]
        ctx = self._get_inventory_context()
        # Si el contexto no tiene snapshot para el location del request, intentar cargar desde DB sería ideal;
        # por ahora usamos fixtures por defecto (determinista)
        shortages = calculate_shortages(items=items, location_id=normalized.location_id, horizon_days=normalized.horizon_days, ctx=ctx)
        catalog = self._get_catalog()
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
            # si no hay supplier, crear línea con missing_data para no bloquear flujo pero marcar como BLOCKED luego
            # fallback: usar shortage qty con precio 0 y missing
            for s in shortages:
                qty = s.shortage_qty if s.shortage_qty > 0 else s.requested_qty
                lines.append(ProposalLine(sku=s.sku, quantity=round(qty, 2), unit=s.unit, unit_price=0.0, currency=normalized.currency))
            missing_supplier.append("fallback_no_supplier_lines_created")

        subtotal = round(sum(li.quantity * li.unit_price for li in lines), 2)
        total = subtotal  # tax 0 Fase 2
        # elegir supplier_id del mejor quote (primera línea)
        # recuperar supplier_id del catalog best_quote nuevamente para evidence
        first_sku = lines[0].sku
        first_qty = lines[0].quantity
        best = catalog.best_quote(sku=first_sku, quantity=first_qty, unit=lines[0].unit, currency=normalized.currency, tenant_id=normalized.tenant_id, location_id=normalized.location_id)
        supplier_id = best.supplier_id if best else "supplier_demo"
        supplier_name = best.supplier_name if best else "Demo Supplier Inc."
        evidence = f"determinista Fase2 — shortages {[s.shortage_qty for s in shortages]} from demand_total {[s.demand_total for s in shortages]}"

        proposal_id = new_id("prop")
        scope_payload = {
            "proposal_id": proposal_id,
            "supplier_id": supplier_id,
            "lines": [{"sku": li.sku, "quantity": li.quantity, "unit_price": li.unit_price} for li in lines],
            "total": total,
            "currency": normalized.currency,
        }
        scope_hash = "sha256:" + hashlib.sha256(json.dumps(scope_payload, sort_keys=True).encode()).hexdigest()

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
        elif any("forecast" in m for m in all_missing) or any(s.shortage_qty > 500 for s in shortages):
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
            all_missing.append(f"policy_blocking:{[c.policy_id for c in checks if c.blocking and c.decision=='fail']}")

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

    def approve_and_complete(self, db: Session, execution_id: str, *, decided_by: str, trace_id: str | None = None) -> Execution:
        row = db.get(WorkflowExecution, execution_id)
        if not row:
            raise ValueError("not found")
        if ExecutionState(row.status) != ExecutionState.AWAITING_APPROVAL:
            raise ValueError(f"cannot approve in state {row.status}")
        appr_dict = row.approval_request
        if not appr_dict:
            raise ValueError("no approval_request")
        # mark approved
        appr = ApprovalRequest.model_validate(appr_dict)
        appr.status = ApprovalStatus.approved
        appr.decided_by = decided_by
        appr.decided_at = utcnow()
        appr.decision_reason = "approved in synthetic flow"
        row.approval_request = appr.model_dump(mode="json")
        db.flush()
        # transitions
        self.transition(db, execution_id, ExecutionState.APPROVED, node="wait_for_human_decision", trace_id=trace_id, actor_type="human", actor_id=decided_by)
        self.transition(db, execution_id, ExecutionState.ACTION_EXECUTED, node="execute_purchase_order", trace_id=trace_id)
        self.transition(db, execution_id, ExecutionState.VERIFIED, node="verify_execution", trace_id=trace_id)
        self.transition(db, execution_id, ExecutionState.COMPLETED, node="summarize_and_close", trace_id=trace_id)
        return self.get_execution(db, execution_id)  # type: ignore
