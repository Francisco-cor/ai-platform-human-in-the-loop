"""FastAPI app — Fase 1-5 skeleton con RAG seguro y aprobación durable."""

from __future__ import annotations

import time
import uuid

from fastapi import Depends, FastAPI, Header, HTTPException, Request, Response
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.orm import Session

from procurement_platform.audit.service import create_audit_event
from procurement_platform.config.settings import get_settings
from procurement_platform.domain.models import (
    ApprovalStatus,
    CreateExecutionRequest,
    NormalizedRequest,
    new_id,
    utcnow,
)
from procurement_platform.observability.logging import configure_logging, get_logger
from procurement_platform.persistence.database import get_db, init_db
from procurement_platform.persistence.models import AuditEventRow, IdempotencyKey, WorkflowExecution
from procurement_platform.workflows.orchestrator import WorkflowOrchestrator, get_rag_service

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger("api")

app = FastAPI(
    title="procurement-platform",
    version="0.1.0",
    description="Enterprise Agentic AI Platform — Fase 0-5 con RAG seguro y aprobación humana durable",
)

orchestrator = WorkflowOrchestrator()

# Ensure tables exist on startup (for sqlite local; postgres handled via alembic)
try:
    init_db()
except Exception as e:
    logger.warning("init_db_failed", error=str(e))


# ---------------------------------------------------------------------------
# Middleware — request_id + trace + logging + payload size
# ---------------------------------------------------------------------------
@app.middleware("http")
async def add_request_context(request: Request, call_next):
    start = time.time()
    request_id = request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex[:12]}"
    trace_id = request.headers.get("traceparent") or uuid.uuid4().hex
    request.state.request_id = request_id
    request.state.trace_id = trace_id

    # payload size guard (F1-4: streaming + content-length)
    cl = request.headers.get("content-length")
    if cl and cl.isdigit():
        if int(cl) > settings.max_payload_bytes:
            return JSONResponse(
                status_code=413,
                content={
                    "code": "payload_too_large",
                    "message": f"payload exceeds limit {settings.max_payload_bytes} bytes",
                    "request_id": request_id,
                },
            )
    # streaming guard: read body and enforce limit even if client omits content-length (chunked)
    if request.method in ("POST", "PUT", "PATCH") and request.url.path.startswith("/v1/"):
        try:
            body = await request.body()
            if len(body) > settings.max_payload_bytes:
                return JSONResponse(
                    status_code=413,
                    content={
                        "code": "payload_too_large",
                        "message": f"payload exceeds limit {settings.max_payload_bytes} bytes (actual {len(body)})",
                        "request_id": request_id,
                    },
                )
            # body is cached by Starlette, downstream will still see it
        except Exception:
            pass

    # Fase 7: rate limit por tenant/IP para POST /v1/procurement/executions
    if request.method == "POST" and request.url.path.startswith("/v1/procurement/executions"):
        try:
            from procurement_platform.security.rate_limiter import get_rate_limiter

            rl = get_rate_limiter()
            # key por tenant si viene en body? usamos IP/tenant placeholder para middleware temprano
            # Intentar extraer tenant del header o IP
            tenant_guess = request.headers.get("X-Tenant-Id", "unknown")
            key = f"api:create_execution:{tenant_guess}"
            # también rate limit global por IP
            ip = request.client.host if request.client else "unknown"
            gkey = f"api:create_execution:ip:{ip}"
            for k in (key, gkey):
                allowed, retry = rl.check(k)
                if not allowed:
                    return JSONResponse(
                        status_code=429,
                        content={
                            "code": "rate_limited",
                            "message": f"rate limit exceeded, retry after {retry:.1f}s",
                            "request_id": request_id,
                            "retry_after": retry,
                        },
                        headers={"Retry-After": str(int(retry) + 1)},
                    )
                # no hit aún; se hará hit después de parsear body en endpoint
                # para middleware solo check, hit en endpoint
        except Exception:
            pass

    response: Response = await call_next(request)
    duration = time.time() - start
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Trace-Id"] = trace_id
    # Fase 7: redactar PII en logs (ya hace logging processor, pero asegurar no loguear body crudo)
    logger.info(
        "request",
        request_id=request_id,
        trace_id=trace_id,
        method=request.method,
        path=request.url.path,
        status_code=response.status_code,
        duration_ms=round(duration * 1000, 2),
    )
    return response


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@app.get("/healthz", tags=["ops"])
def healthz():
    return {"status": "ok", "version": app.version, "env": settings.app_env}


@app.get("/readyz", tags=["ops"])
def readyz(db: Session = Depends(get_db)):
    # check DB
    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
    except Exception as e:
        raise HTTPException(status_code=503, detail={"code": "db_not_ready", "message": str(e)})
    # redis optional — try ping if configured
    redis_ok = None
    try:
        import redis

        r = redis.from_url(settings.redis_url, socket_connect_timeout=1)
        r.ping()
        redis_ok = True
    except Exception:
        redis_ok = False  # not fatal for Fase 1
    return {"status": "ready", "db": "ok", "redis": "ok" if redis_ok else "skipped"}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _normalize(req: CreateExecutionRequest, request_id: str) -> NormalizedRequest:
    # Fase 7: redactar PII en raw_intent antes de normalizar (no persistir PII cruda)
    raw = req.raw_intent
    if raw:
        try:
            from procurement_platform.security.pii import redact_pii

            redacted, det = redact_pii(raw)
            if det["has_pii"]:
                raw = redacted
        except Exception:
            pass
    # si items no viene, usamos intent para generar 1 item sintético
    items = req.items
    if not items:
        # Fase 1: si raw_intent existe, mapeamos a 1 item demo
        items = [{"sku": "MAT-001", "quantity": 120, "unit": "piece"}]  # type: ignore
    # Pydantic will coerce
    return NormalizedRequest(
        request_id=request_id,
        tenant_id=req.tenant_id,
        requester_id=req.requester_id,
        items=items,  # type: ignore
        horizon_days=req.horizon_days,
        location_id=req.location_id,
        currency=req.currency,
        source=req.source,
        created_at=utcnow(),
        raw_intent=raw,
        idempotency_key=req.idempotency_key,
    )


# ---------------------------------------------------------------------------
# Executions
# ---------------------------------------------------------------------------
@app.post("/v1/procurement/executions", status_code=202, tags=["procurement"])
def create_execution(
    payload: CreateExecutionRequest,
    request: Request,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    # Fase 7: rate limit hit por tenant (después de parsear payload)
    try:
        from procurement_platform.security.rate_limiter import get_rate_limiter

        rl = get_rate_limiter()
        rl.check_and_hit(f"api:create_execution:{payload.tenant_id}")
        ip = request.client.host if request.client else "unknown"
        rl.check_and_hit(f"api:create_execution:ip:{ip}")
    except Exception as e:
        if "rate_limited" in str(e):
            raise HTTPException(status_code=429, detail={"code": "rate_limited", "message": str(e)})
        # no bloquear por errores de limiter
        pass
    # idempotency: buscar key
    key = idempotency_key or payload.idempotency_key
    if key:
        existing = db.get(IdempotencyKey, key)
        if existing and existing.response_json:
            return JSONResponse(status_code=202, content=existing.response_json)

    request_id = payload.request_id or request.state.request_id
    # ensure request_id is req_*
    if not request_id.startswith("req_"):
        request_id = f"req_{uuid.uuid4().hex[:12]}"

    normalized = _normalize(payload, request_id)
    trace_id = request.state.trace_id

    # validation: amount etc. already via Pydantic
    exec_obj = orchestrator.create_execution(
        db, normalized=normalized, trace_id=trace_id, actor_id=normalized.requester_id
    )
    # F2-2: async path enqueues worker, otherwise advance sync (Fase 1)
    try:
        from procurement_platform.config.settings import get_settings

        if get_settings().async_enabled:
            from procurement_platform.workers.tasks import enqueue_workflow

            enqueued = enqueue_workflow(exec_obj.execution_id, trace_id=trace_id)
            if not enqueued:
                # fallback sync if redis unavailable
                exec_obj = orchestrator.advance_synthetic(
                    db, exec_obj.execution_id, trace_id=trace_id
                )
            else:
                # enqueued: refresh to show RECEIVED/NORMALIZED; worker will advance async
                # for API contract, return current state (RECEIVED) and let client poll
                pass
        else:
            exec_obj = orchestrator.advance_synthetic(db, exec_obj.execution_id, trace_id=trace_id)
    except Exception:
        # ensure sync fallback never blocks creation
        try:
            exec_obj = orchestrator.advance_synthetic(db, exec_obj.execution_id, trace_id=trace_id)
        except Exception:
            pass

    resp_body = {
        "execution_id": exec_obj.execution_id,
        "request_id": exec_obj.request_id,
        "status": exec_obj.status.value,
        "current_node": exec_obj.current_node,
        "approval_request": exec_obj.approval_request.model_dump(mode="json")
        if exec_obj.approval_request
        else None,
        "created_at": exec_obj.created_at.isoformat(),
    }

    if key:
        db.add(
            IdempotencyKey(
                key=key,
                scope="create_execution",
                execution_id=exec_obj.execution_id,
                response_json=resp_body,
                created_at=utcnow(),
            )
        )
        db.commit()

    return JSONResponse(status_code=202, content=resp_body)


@app.get("/v1/procurement/executions/{execution_id}", tags=["procurement"])
def get_execution(execution_id: str, db: Session = Depends(get_db)):
    # Fase 5: auto-expirar si corresponde al consultar (reanudación durable)
    row = db.get(WorkflowExecution, execution_id)
    if row:
        try:
            orchestrator._check_and_expire_if_needed(db, row, trace_id=None)
        except Exception:
            pass
    exec_obj = orchestrator.get_execution(db, execution_id)
    if not exec_obj:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "execution not found"}
        )
    return {
        "execution_id": exec_obj.execution_id,
        "request_id": exec_obj.request_id,
        "tenant_id": exec_obj.tenant_id,
        "status": exec_obj.status.value,
        "current_node": exec_obj.current_node,
        "normalized_request": exec_obj.normalized_request.model_dump(mode="json")
        if exec_obj.normalized_request
        else None,
        "proposal": exec_obj.proposal.model_dump(mode="json") if exec_obj.proposal else None,
        "approval_request": exec_obj.approval_request.model_dump(mode="json")
        if exec_obj.approval_request
        else None,
        "created_at": exec_obj.created_at.isoformat(),
        "updated_at": exec_obj.updated_at.isoformat(),
        "trace_id": exec_obj.trace_id,
    }


@app.get("/v1/procurement/executions/{execution_id}/events", tags=["procurement"])
def list_events(
    execution_id: str,
    db: Session = Depends(get_db),
    limit: int = 50,
    cursor: str | None = None,
):
    # verify execution exists
    if not db.get(WorkflowExecution, execution_id):
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "execution not found"}
        )
    # clamp limit 1..100
    limit = max(1, min(limit, 100))
    # stable ordering: timestamp asc, event_id asc (deterministic for same timestamp)
    base_q = (
        db.query(AuditEventRow)
        .filter(AuditEventRow.execution_id == execution_id)
        .order_by(AuditEventRow.timestamp.asc(), AuditEventRow.event_id.asc())
    )
    # total count for header
    total = base_q.count()
    q = base_q
    # cursor: event_id — find timestamp and filter > timestamp or = timestamp and id > cursor
    if cursor:
        cur_row = db.get(AuditEventRow, cursor)
        if cur_row:
            # stable cursor: rows with timestamp > cur.timestamp OR (timestamp == cur.timestamp AND event_id > cur.event_id)
            q = q.filter(
                (AuditEventRow.timestamp > cur_row.timestamp)
                | (
                    (AuditEventRow.timestamp == cur_row.timestamp)
                    & (AuditEventRow.event_id > cur_row.event_id)
                )
            )
    rows = q.limit(limit).all()
    # has_more: if we got limit rows and total > offset, check if more remain
    has_more = len(rows) == limit and total > len(rows)
    # if cursor present, need to check remaining beyond rows
    if cursor and rows:
        # compute remaining count heuristic: total filtered vs rows
        has_more = base_q.filter(
            AuditEventRow.timestamp > rows[-1].timestamp
        ).count() > 0 or base_q.filter(
            (AuditEventRow.timestamp == rows[-1].timestamp)
            & (AuditEventRow.event_id > rows[-1].event_id)
        ).count() > 0
        # simplify: if we got limit rows, assume may have more
        has_more = len(rows) == limit
    next_cursor = rows[-1].event_id if has_more else None
    return {
        "execution_id": execution_id,
        "count": len(rows),
        "total": total,
        "limit": limit,
        "has_more": has_more,
        "events": [
            {
                "event_id": r.event_id,
                "event_type": r.event_type,
                "actor_type": r.actor_type,
                "actor_id": r.actor_id,
                "tool_name": r.tool_name,
                "timestamp": r.timestamp.isoformat(),
                "trace_id": r.trace_id,
                "details": r.details,
            }
            for r in rows
        ],
        "next_cursor": next_cursor,
    }


# ---------------------------------------------------------------------------
# Approvals — Fase 5 con snapshot inmutable, expiración, scope_hash y doble aprobación
# ---------------------------------------------------------------------------
@app.get("/v1/approvals/{approval_id}", tags=["approvals"])
def get_approval(approval_id: str, db: Session = Depends(get_db)):
    # buscar por approval_id (scan Fase 1-5)
    rows = db.query(WorkflowExecution).all()
    for row in rows:
        appr = row.approval_request
        if appr and appr.get("approval_id") == approval_id:
            # verificar expiración automática al consultar
            try:
                orchestrator._check_and_expire_if_needed(db, row, trace_id=None)
                db.refresh(row)
                appr = row.approval_request
            except Exception:
                pass
            proposal = row.proposal
            return {
                "approval_id": approval_id,
                "execution_id": row.execution_id,
                "request_id": row.request_id,
                "status": appr.get("status"),
                "scope_hash": appr.get("scope_hash"),
                "proposal_snapshot": appr.get("proposal_snapshot"),
                "proposal_current": proposal,
                "risk_level": appr.get("risk_level"),
                "total": appr.get("total"),
                "currency": appr.get("currency"),
                "required_approvals": appr.get("required_approvals", 1),
                "approvals_received": appr.get("approvals_received", 0),
                "approvers": appr.get("approvers", []),
                "requested_by": appr.get("requested_by"),
                "requested_at": appr.get("requested_at"),
                "expires_at": appr.get("expires_at"),
                "decided_by": appr.get("decided_by"),
                "decision_reason": appr.get("decision_reason"),
                "decided_at": appr.get("decided_at"),
                "execution_status": row.status,
                "current_node": row.current_node,
            }
    raise HTTPException(
        status_code=404, detail={"code": "not_found", "message": "approval not found"}
    )


@app.post("/v1/approvals/{approval_id}/decision", tags=["approvals"])
def decide_approval(
    approval_id: str,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    # payload = {decision, decided_by, reason, scope_hash?}
    decision = payload.get("decision")
    decided_by = payload.get("decided_by")
    reason = payload.get("reason")
    scope_hash = payload.get("scope_hash")  # opcional — validación extra Fase 5
    if decision not in {"approved", "rejected", "needs_changes"}:
        raise HTTPException(
            status_code=400, detail={"code": "validation_error", "message": "invalid decision"}
        )
    if not decided_by:
        raise HTTPException(
            status_code=400, detail={"code": "validation_error", "message": "decided_by required"}
        )

    # idempotency check — Fase 5: incluye decision en key? usamos key tal cual si existe
    if idempotency_key:
        existing = db.get(IdempotencyKey, idempotency_key)
        if existing and existing.response_json:
            return existing.response_json

    # find execution by approval_id
    rows = db.query(WorkflowExecution).all()
    target_exec = None
    for row in rows:
        appr = row.approval_request
        if appr and appr.get("approval_id") == approval_id:
            target_exec = row
            break
    if not target_exec:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "approval not found"}
        )

    execution_id = target_exec.execution_id
    trace_id = request.state.trace_id

    # Fase 5: verificación scope_hash si se provee (detecta changed_after_approval desde cliente)
    if scope_hash:
        current_appr = target_exec.approval_request or {}
        if scope_hash != current_appr.get("scope_hash"):
            raise HTTPException(
                status_code=409,
                detail={
                    "code": "scope_mismatch",
                    "message": f"scope_hash mismatch: provided {scope_hash} != approval {current_appr.get('scope_hash')}",
                    "details": {"provided": scope_hash, "expected": current_appr.get("scope_hash")},
                },
            )

    # delegar a orchestrator según decisión — maneja expiración, doble aprobación, locks, gateway
    try:
        if decision == "approved":
            exec_obj = orchestrator.approve_and_complete(
                db, execution_id, decided_by=decided_by, trace_id=trace_id, decision_reason=reason
            )
            # detectar parcial (doble aprobación pendiente)
            appr_after = exec_obj.approval_request
            if (
                appr_after
                and appr_after.status == ApprovalStatus.pending
                and appr_after.approvals_received < appr_after.required_approvals
            ):
                resp = {
                    "approval_id": approval_id,
                    "execution_id": execution_id,
                    "status": "partially_approved",
                    "approvers": appr_after.approvers,
                    "required_approvals": appr_after.required_approvals,
                    "approvals_received": appr_after.approvals_received,
                    "execution_status": exec_obj.status.value,
                    "message": f"requires {appr_after.required_approvals} approvals, received {appr_after.approvals_received}",
                }
            else:
                resp = {
                    "approval_id": approval_id,
                    "execution_id": execution_id,
                    "status": "approved",
                    "execution_status": exec_obj.status.value,
                }
        elif decision == "rejected":
            exec_obj = orchestrator.reject_execution(
                db, execution_id, decided_by=decided_by, trace_id=trace_id, reason=reason
            )
            resp = {
                "approval_id": approval_id,
                "execution_id": execution_id,
                "status": "rejected",
                "execution_status": exec_obj.status.value,
            }
        else:  # needs_changes
            exec_obj = orchestrator.request_changes(
                db, execution_id, decided_by=decided_by, trace_id=trace_id, reason=reason
            )
            resp = {
                "approval_id": approval_id,
                "execution_id": execution_id,
                "status": "needs_changes",
                "execution_status": exec_obj.status.value,
            }
    except ValueError as e:
        msg = str(e)
        # mapear a HTTP codes Fase 5
        if "expired" in msg:
            # verificar si ya transicionó a EXPIRED
            raise HTTPException(status_code=409, detail={"code": "expired", "message": msg})
        if "scope_mismatch" in msg:
            raise HTTPException(status_code=409, detail={"code": "scope_mismatch", "message": msg})
        if "already" in msg.lower() and "approved" in msg.lower():
            raise HTTPException(status_code=409, detail={"code": "already_decided", "message": msg})
        if "already" in msg.lower():
            raise HTTPException(status_code=409, detail={"code": "already_decided", "message": msg})
        if "locked" in msg.lower() or "conflict" in msg.lower():
            raise HTTPException(status_code=409, detail={"code": "conflict", "message": msg})
        if "cannot approve" in msg.lower() or "cannot reject" in msg.lower():
            raise HTTPException(status_code=409, detail={"code": "invalid_state", "message": msg})
        raise HTTPException(status_code=400, detail={"code": "validation_error", "message": msg})

    if idempotency_key:
        db.add(
            IdempotencyKey(
                key=idempotency_key,
                scope="approval_decision",
                execution_id=execution_id,
                response_json=resp,
                created_at=utcnow(),
            )
        )
        db.commit()

    return resp


@app.post("/v1/procurement/executions/{execution_id}/resume", tags=["procurement"])
def resume_execution(execution_id: str, request: Request, db: Session = Depends(get_db)):
    """Fase 5 — reanudación durable tras reinicio/timeout.

    Idempotente: si ya está COMPLETED, no duplica orden.
    Si está AWAITING_APPROVAL con aprobación válida, ejecuta orden.
    Si está APPROVED sin ejecutar, ejecuta y verifica.
    """
    row = db.get(WorkflowExecution, execution_id)
    if not row:
        raise HTTPException(
            status_code=404, detail={"code": "not_found", "message": "execution not found"}
        )
    trace_id = request.state.trace_id
    try:
        exec_obj = orchestrator.resume_durable(db, execution_id, trace_id=trace_id)
    except Exception as e:
        raise HTTPException(status_code=409, detail={"code": "resume_failed", "message": str(e)})
    return {
        "execution_id": exec_obj.execution_id,
        "status": exec_obj.status.value,
        "current_node": exec_obj.current_node,
        "approval_request": exec_obj.approval_request.model_dump(mode="json")
        if exec_obj.approval_request
        else None,
        "proposal": exec_obj.proposal.model_dump(mode="json") if exec_obj.proposal else None,
    }


@app.post("/v1/documents", tags=["documents"])
def upload_document(payload: dict, request: Request, db: Session = Depends(get_db)):
    """Ingesta RAG Fase 3: valida, clasifica, detecta injection y genera embeddings."""
    from datetime import UTC, datetime

    from procurement_platform.rag.models import Document, DocumentMetadata

    # payload esperado: {tenant_id, title, content, doc_type, classification, jurisdiction, version, valid_from, valid_to, ...}
    tenant_id = payload.get("tenant_id", "tenant_demo")
    title = payload.get("title", "Untitled")
    content = payload.get("content", "")
    if not content or len(content.strip()) < 10:
        raise HTTPException(
            status_code=400, detail={"code": "validation_error", "message": "content too short"}
        )

    doc_type = payload.get("doc_type", "policy")
    classification = payload.get("classification", "internal")
    jurisdiction = payload.get("jurisdiction", "global")
    version = payload.get("version", "1.0.0")
    location_id = payload.get("location_id")

    # parse valid_from/to if provided as ISO
    def parse_dt(v):
        if not v:
            return None
        try:
            return datetime.fromisoformat(v.replace("Z", "+00:00"))
        except Exception:
            return None

    valid_from = parse_dt(payload.get("valid_from")) or datetime.now(UTC)
    valid_to = parse_dt(payload.get("valid_to"))

    metadata = DocumentMetadata(
        document_id=payload.get("document_id") or new_id("doc"),
        tenant_id=tenant_id,
        title=title,
        doc_type=doc_type,  # type: ignore
        classification=classification,  # type: ignore
        jurisdiction=jurisdiction,
        location_id=location_id,
        version=version,
        valid_from=valid_from,
        valid_to=valid_to,
        status=payload.get("status", "approved"),  # type: ignore
        allowed_tenants=payload.get("allowed_tenants", [tenant_id]),
    )
    document = Document(metadata=metadata, content=content, pages=payload.get("pages", []))

    rag = get_rag_service()
    status, chunks = rag.ingest_document(
        document=document,
        filename=payload.get("filename"),
        actor_id=request.state.request_id,
        db=db,
    )

    # audit
    create_audit_event(
        db,
        execution_id="no_exec",
        request_id=request.state.request_id,
        event_type=f"rag.ingestion.{status}",
        actor_type="system",
        actor_id="rag_service",
        trace_id=request.state.trace_id,
        details={
            "document_id": metadata.document_id,
            "chunks": len(chunks),
            "status": status,
            "security_flags": document.metadata.security_flags,
        },
    )
    db.commit()

    http_status = (
        200
        if status == "indexed"
        else 409
        if status == "duplicate"
        else 422
        if status in ("rejected", "quarantined")
        else 200
    )
    return JSONResponse(
        status_code=http_status,
        content={
            "status": status,
            "document_id": metadata.document_id,
            "chunks_created": len(chunks),
            "content_hash": document.metadata.content_hash,
            "security_flags": document.metadata.security_flags,
            "is_malicious": document.metadata.is_malicious,
            "request_id": request.state.request_id,
        },
    )


@app.get("/v1/rag/search", tags=["rag"])
def rag_search(
    query: str,
    tenant_id: str = "tenant_demo",
    location_id: str | None = None,
    jurisdiction: str | None = None,
    top_k: int = 5,
    db: Session = Depends(get_db),
):
    """Endpoint de debug para RAG — Fase 3 (no expone documentos fuera de permiso)."""
    rag = get_rag_service()
    res = rag.retrieve(
        query=query,
        tenant_id=tenant_id,
        location_id=location_id,
        jurisdiction=jurisdiction,
        top_k=top_k,
    )
    # no exponer texto completo si es confidencial; aquí retornamos citas y scores
    return {
        "query": query,
        "count": res["count"],
        "results": [
            {
                "citation": r.citation,
                "score": r.score,
                "reliability": r.chunk.metadata.reliability,
                "is_malicious": r.chunk.metadata.is_malicious,
                "text_preview": r.chunk.text[:200],
            }
            for r in res["results"]
        ],
        "warnings": res["warnings"],
        "conflict": res["conflict"],
    }


# ---------------------------------------------------------------------------
# Error handlers — formato estándar {code, message, request_id, details}
# ---------------------------------------------------------------------------
@app.exception_handler(HTTPException)
async def http_exception_handler(request: Request, exc: HTTPException):
    request_id = getattr(request.state, "request_id", None)
    if isinstance(exc.detail, dict) and "code" in exc.detail:
        content = {**exc.detail, "request_id": request_id}
        # ensure message field
        if "message" not in content and "msg" in content:
            content["message"] = content.pop("msg")
    else:
        content = {"code": "error", "message": str(exc.detail), "request_id": request_id}
    return JSONResponse(status_code=exc.status_code, content=content)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(request: Request, exc: RequestValidationError):
    request_id = getattr(request.state, "request_id", None)
    return JSONResponse(
        status_code=422,
        content={
            "code": "validation_error",
            "message": "invalid request payload",
            "request_id": request_id,
            "details": exc.errors(),
        },
    )


# For tests to override get_db easily
__all__ = ["app"]
