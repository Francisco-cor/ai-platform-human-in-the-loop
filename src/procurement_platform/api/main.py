"""FastAPI app — Fase 1 esqueleto ejecutable."""
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
    ExecutionState,
    NormalizedRequest,
    new_id,
    utcnow,
)
from procurement_platform.observability.logging import configure_logging, get_logger
from procurement_platform.persistence.database import get_db, init_db
from procurement_platform.persistence.models import AuditEventRow, IdempotencyKey, WorkflowExecution
from procurement_platform.workflows.orchestrator import WorkflowOrchestrator

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger("api")

app = FastAPI(
    title="procurement-platform",
    version="0.1.0",
    description="Enterprise Agentic AI Platform — Fase 0-1 skeleton",
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

    # payload size guard
    cl = request.headers.get("content-length")
    if cl and cl.isdigit():
        if int(cl) > settings.max_payload_bytes:
            return JSONResponse(
                status_code=413,
                content={"code": "payload_too_large", "message": "payload exceeds limit", "request_id": request_id},
            )

    response: Response = await call_next(request)
    duration = time.time() - start
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Trace-Id"] = trace_id
    # structlog
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
        raw_intent=req.raw_intent,
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
    exec_obj = orchestrator.create_execution(db, normalized=normalized, trace_id=trace_id, actor_id=normalized.requester_id)
    # Avanzar sintéticamente hasta AWAITING_APPROVAL (Fase 1)
    exec_obj = orchestrator.advance_synthetic(db, exec_obj.execution_id, trace_id=trace_id)

    resp_body = {
        "execution_id": exec_obj.execution_id,
        "request_id": exec_obj.request_id,
        "status": exec_obj.status.value,
        "current_node": exec_obj.current_node,
        "approval_request": exec_obj.approval_request.model_dump(mode="json") if exec_obj.approval_request else None,
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
    exec_obj = orchestrator.get_execution(db, execution_id)
    if not exec_obj:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "execution not found"})
    return {
        "execution_id": exec_obj.execution_id,
        "request_id": exec_obj.request_id,
        "tenant_id": exec_obj.tenant_id,
        "status": exec_obj.status.value,
        "current_node": exec_obj.current_node,
        "normalized_request": exec_obj.normalized_request.model_dump(mode="json") if exec_obj.normalized_request else None,
        "proposal": exec_obj.proposal.model_dump(mode="json") if exec_obj.proposal else None,
        "approval_request": exec_obj.approval_request.model_dump(mode="json") if exec_obj.approval_request else None,
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
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "execution not found"})
    q = db.query(AuditEventRow).filter(AuditEventRow.execution_id == execution_id).order_by(AuditEventRow.timestamp.asc())
    # naive cursor: event_id
    if cursor:
        # find timestamp of cursor event
        cur_row = db.get(AuditEventRow, cursor)
        if cur_row:
            q = q.filter(AuditEventRow.timestamp > cur_row.timestamp)
    rows = q.limit(min(limit, 100)).all()
    next_cursor = rows[-1].event_id if len(rows) == min(limit, 100) else None
    return {
        "execution_id": execution_id,
        "count": len(rows),
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
# Approvals
# ---------------------------------------------------------------------------
@app.post("/v1/approvals/{approval_id}/decision", tags=["approvals"])
def decide_approval(
    approval_id: str,
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    idempotency_key: str | None = Header(default=None, alias="Idempotency-Key"),
):
    # payload = {decision, decided_by, reason}
    decision = payload.get("decision")
    decided_by = payload.get("decided_by")
    reason = payload.get("reason")
    if decision not in {"approved", "rejected", "needs_changes"}:
        raise HTTPException(status_code=400, detail={"code": "validation_error", "message": "invalid decision"})
    if not decided_by:
        raise HTTPException(status_code=400, detail={"code": "validation_error", "message": "decided_by required"})

    # idempotency check
    if idempotency_key:
        existing = db.get(IdempotencyKey, idempotency_key)
        if existing and existing.response_json:
            return existing.response_json

    # find execution by approval_id
    # scan workflow_executions (Fase 1 small; future index)
    executions = db.query(WorkflowExecution).all()
    target_exec = None
    for row in executions:
        appr = row.approval_request
        if appr and appr.get("approval_id") == approval_id:
            target_exec = row
            break
    if not target_exec:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "approval not found"})

    execution_id = target_exec.execution_id
    trace_id = request.state.trace_id

    # Verify not expired
    appr_dict = target_exec.approval_request
    expires_at = appr_dict.get("expires_at")
    if expires_at:
        # parse iso
        from datetime import datetime

        exp = datetime.fromisoformat(expires_at.replace("Z", "+00:00"))
        if utcnow() > exp and decision == "approved":
            raise HTTPException(status_code=409, detail={"code": "expired", "message": "approval expired"})

    if decision == "approved":
        # scope_hash check already in orchestrator transition
        exec_obj = orchestrator.approve_and_complete(db, execution_id, decided_by=decided_by, trace_id=trace_id)
        # also record human decision audit
        create_audit_event(
            db,
            execution_id=execution_id,
            request_id=exec_obj.request_id,
            event_type="approval.decided",
            actor_type="human",
            actor_id=decided_by,
            trace_id=trace_id,
            details={"approval_id": approval_id, "decision": decision, "reason": reason},
        )
        db.commit()
        resp = {
            "approval_id": approval_id,
            "execution_id": execution_id,
            "status": "approved",
            "execution_status": exec_obj.status.value,
        }
    elif decision == "rejected":
        # mark rejected and transition to REJECTED
        appr_obj = target_exec.approval_request
        appr_obj["status"] = ApprovalStatus.rejected.value  # type: ignore
        appr_obj["decided_by"] = decided_by  # type: ignore
        appr_obj["decision_reason"] = reason  # type: ignore
        target_exec.approval_request = appr_obj
        db.flush()
        exec_obj = orchestrator.transition(
            db, execution_id, ExecutionState.REJECTED, node="wait_for_human_decision", trace_id=trace_id, actor_type="human", actor_id=decided_by
        )
        resp = {"approval_id": approval_id, "execution_id": execution_id, "status": "rejected", "execution_status": exec_obj.status.value}
    else:  # needs_changes
        exec_obj = orchestrator.transition(
            db, execution_id, ExecutionState.NEEDS_CLARIFICATION, node="wait_for_human_decision", trace_id=trace_id, actor_type="human", actor_id=decided_by
        )
        resp = {"approval_id": approval_id, "execution_id": execution_id, "status": "needs_changes", "execution_status": exec_obj.status.value}

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


@app.post("/v1/documents", tags=["documents"])
def upload_document(payload: dict, request: Request):
    # Fase 1 stub — validates size and returns accepted
    return {"status": "accepted", "document_id": new_id("doc"), "request_id": request.state.request_id}


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
