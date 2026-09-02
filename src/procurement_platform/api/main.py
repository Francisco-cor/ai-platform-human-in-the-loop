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
from procurement_platform.security.auth import Principal, get_current_principal
from procurement_platform.workflows.orchestrator import WorkflowOrchestrator, get_rag_service

settings = get_settings()
configure_logging(settings.log_level)
logger = get_logger("api")

app = FastAPI(
    title="procurement-platform",
    version="0.1.0",
    description="Enterprise Agentic AI Platform — Fase 0-5 con RAG seguro y aprobación humana durable",
)

# F5-1: OTEL tracing auto-instrumented
try:
    from procurement_platform.observability.tracing import setup_tracing

    setup_tracing(app, settings_exporter=settings.otel_exporter)
except Exception as _e:
    logger.warning("tracing_setup_failed", error=str(_e))

orchestrator = WorkflowOrchestrator()

# Ensure tables exist on startup (for sqlite local; postgres handled via alembic)
try:
    init_db()
except Exception as e:
    logger.warning("init_db_failed", error=str(e))


# ---------------------------------------------------------------------------
# Middleware — request_id + trace + logging + payload size + OTEL span (F5-1)
# ---------------------------------------------------------------------------
@app.middleware("http")
async def add_request_context(request: Request, call_next):
    start = time.time()
    request_id = request.headers.get("X-Request-Id") or f"req_{uuid.uuid4().hex[:12]}"
    # F5-1: traceparent W3C or PROCUREMENT_OTEL_EXPORTER; fallback to hex
    trace_id = (
        request.headers.get("traceparent") or request.headers.get("X-Trace-Id") or uuid.uuid4().hex
    )
    # normalize traceparent if it contains version-trace-span-flags
    if "-" in trace_id and len(trace_id) > 32:
        try:
            parts = trace_id.split("-")
            if len(parts) >= 2:
                trace_id = parts[1]
        except Exception:
            pass
    request.state.request_id = request_id
    request.state.trace_id = trace_id
    # bind to contextvars for logging correlation
    try:
        from procurement_platform.observability.logging import (
            request_id_ctx,
            span_id_ctx,
            trace_id_ctx,
        )

        request_id_ctx.set(request_id)
        trace_id_ctx.set(trace_id)
        # span_id will be set inside OTEL span if available
        span_id_ctx.set(uuid.uuid4().hex[:16])
    except Exception:
        pass

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

    # F5-1: OTEL span per request (lazy, no-op if exporter none)
    try:
        from opentelemetry import trace as otel_trace  # type: ignore

        tracer = otel_trace.get_tracer("procurement_platform.api")
        # use start_as_current_span if available, else direct call
        span_name = f"{request.method} {request.url.path}"
        with tracer.start_as_current_span(span_name) as span:  # type: ignore
            # correlate trace_id/span_id to contextvars and request.state
            try:
                sc = span.get_span_context()
                if sc and getattr(sc, "is_valid", False):
                    otel_tid = format(sc.trace_id, "032x")
                    otel_sid = format(sc.span_id, "016x")
                    request.state.trace_id = otel_tid
                    trace_id = otel_tid
                    try:
                        from procurement_platform.observability.logging import (
                            span_id_ctx,
                            trace_id_ctx,
                        )

                        trace_id_ctx.set(otel_tid)
                        span_id_ctx.set(otel_sid)
                    except Exception:
                        pass
                    span.set_attribute("request_id", request_id)
                    span.set_attribute("http.method", request.method)
                    span.set_attribute("http.url", str(request.url))
            except Exception:
                pass
            response: Response = await call_next(request)
            duration = time.time() - start
            response.headers["X-Request-Id"] = request_id
            response.headers["X-Trace-Id"] = trace_id
            try:
                span.set_attribute("http.status_code", response.status_code)
                span.set_attribute("duration_ms", round(duration * 1000, 2))
            except Exception:
                pass
            # F5-2: metrics observe
            try:
                from procurement_platform.observability.metrics import get_metrics

                get_metrics().observe_http(
                    method=request.method,
                    path=request.url.path,
                    status=response.status_code,
                    duration_s=duration,
                )
            except Exception:
                pass
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
    except Exception:
        # fallback without OTEL (no-op tracer or import error)
        pass

    response: Response = await call_next(request)
    duration = time.time() - start
    response.headers["X-Request-Id"] = request_id
    response.headers["X-Trace-Id"] = trace_id
    # F5-2: metrics even without OTEL
    try:
        from procurement_platform.observability.metrics import get_metrics

        get_metrics().observe_http(
            method=request.method,
            path=request.url.path,
            status=response.status_code,
            duration_s=duration,
        )
    except Exception:
        pass
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


@app.get("/metrics", tags=["ops"])
def metrics_endpoint():
    """F5-2 Prometheus metrics exposition."""
    from fastapi.responses import PlainTextResponse

    from procurement_platform.observability.metrics import get_metrics

    data = get_metrics().generate()
    return PlainTextResponse(data, media_type="text/plain; version=0.0.4")


@app.get("/readyz", tags=["ops"])
def readyz(db: Session = Depends(get_db)):
    """F5-6 deep health: DB + Redis + vector (pgvector)."""
    checks: dict[str, str] = {}
    # DB
    try:
        db.execute(__import__("sqlalchemy").text("SELECT 1"))
        checks["db"] = "ok"
    except Exception as e:
        raise HTTPException(status_code=503, detail={"code": "db_not_ready", "message": str(e)})
    # Redis
    try:
        import redis

        r = redis.from_url(settings.redis_url, socket_connect_timeout=1)
        r.ping()
        checks["redis"] = "ok"
    except Exception:
        checks["redis"] = "skipped"
    # Vector (pgvector) — check extension and column
    try:
        from procurement_platform.persistence.database import get_engine
        from sqlalchemy import inspect as sa_inspect

        eng = get_engine()
        insp = sa_inspect(eng)
        cols = (
            {c["name"] for c in insp.get_columns("document_chunks")}
            if insp.has_table("document_chunks")
            else set()
        )
        if "embedding_vec" in cols:
            checks["vector"] = "ok"
            # try vector extension check on postgres
            if eng.dialect.name == "postgresql":
                try:
                    db.execute(
                        __import__("sqlalchemy").text(
                            "SELECT 1 FROM pg_extension WHERE extname='vector'"
                        )
                    )
                    checks["vector"] = "ok"
                except Exception:
                    checks["vector"] = "degraded"
        else:
            checks["vector"] = "skipped"
    except Exception:
        checks["vector"] = "skipped"
    # RAG service
    try:
        from procurement_platform.workflows.orchestrator import get_rag_service

        rag = get_rag_service()
        checks["rag"] = "ok" if rag is not None else "skipped"
    except Exception:
        checks["rag"] = "skipped"
    # tracing
    try:
        from procurement_platform.observability.tracing import get_current_span_context

        tid, sid = get_current_span_context()
        checks["tracing"] = "ok" if tid else "skipped"
    except Exception:
        checks["tracing"] = "skipped"
    # overall ready if db ok
    status = "ready" if checks.get("db") == "ok" else "degraded"
    return {"status": status, **checks}


@app.get("/slo", tags=["ops"])
def slo():
    """F5-6 SLO burn rate — http 5xx, p95, approval backlog, budget."""
    try:
        from procurement_platform.observability.metrics import get_metrics

        m = get_metrics()
        # estimate error rate from http_requests_total
        total = 0
        errors = 0
        try:
            for key, val in m.http_requests_total._data.items():
                # key is (method,path,status)
                status = key[2] if len(key) > 2 else ""
                total += val
                if status.startswith("5"):
                    errors += val
        except Exception:
            pass
        error_rate = (errors / total) if total else 0.0
        # burn rate: error_rate / (1 - 0.999) for 99.9 SLO
        burn = error_rate / 0.001 if error_rate else 0.0
        # backlog approvals from gauge
        backlog = 0
        try:
            for v in m.approval_pending._data.values():
                backlog += v
        except Exception:
            pass
        # p95 placeholder: compute from histogram buckets if available
        p95_placeholder = 0.5
        return {
            "slo": "99.9% availability, p95<1s, backlog<50",
            "error_rate": round(error_rate, 4),
            "burn_rate": round(burn, 3),
            "approval_backlog": int(backlog),
            "p95_latency_s": p95_placeholder,
            "status": "ok" if error_rate < 0.01 and backlog < 50 else "degraded",
        }
    except Exception as e:
        return {"status": "unknown", "error": str(e)}


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
    principal: Principal = Depends(get_current_principal),
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
    # F3-1: tenant isolation via principal (if authenticated, must match payload tenant)
    if principal.auth_method != "anonymous" and principal.tenant_id != payload.tenant_id:
        raise HTTPException(
            status_code=403,
            detail={
                "code": "tenant_forbidden",
                "message": f"principal tenant {principal.tenant_id} != payload {payload.tenant_id}",
            },
        )
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


@app.get("/v1/procurement/executions", tags=["procurement"])
def list_executions(
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
    tenant_id: str | None = None,
    tenant: str | None = None,
    state: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
):
    """Fase 8 — lista paginada estable: total_count, page_size, has_more, orden created_at asc, execution_id asc."""
    effective_tenant = tenant_id or tenant or (principal.tenant_id if principal.auth_method != "anonymous" else None)
    # clamp limit 1..100
    limit = max(1, min(limit, 100))
    # Fase 9 — soft-delete: hide soft-deleted tenants unless include_deleted
    # For list, we filter out soft-deleted tenants
    try:
        from procurement_platform.persistence.retention import is_tenant_soft_deleted
        # If effective_tenant is soft-deleted, return empty
        if effective_tenant and is_tenant_soft_deleted(db, effective_tenant):
            return {"count": 0, "total_count": 0, "page_size": limit, "has_more": False, "next_cursor": None, "executions": []}
    except Exception:
        pass
    base_q = db.query(WorkflowExecution)
    if effective_tenant:
        base_q = base_q.filter(WorkflowExecution.tenant_id == effective_tenant)
    if state and state != "all":
        base_q = base_q.filter(WorkflowExecution.status == state)
    # stable order: created_at asc, execution_id asc
    base_q = base_q.order_by(WorkflowExecution.created_at.asc(), WorkflowExecution.execution_id.asc())
    total_count = base_q.count()
    # cursor: execution_id
    if cursor:
        cur_row = db.get(WorkflowExecution, cursor)
        if cur_row:
            # filter where (created_at > cur_created) or (created_at == cur_created and execution_id > cursor)
            base_q = base_q.filter(
                (WorkflowExecution.created_at > cur_row.created_at)
                | ((WorkflowExecution.created_at == cur_row.created_at) & (WorkflowExecution.execution_id > cur_row.execution_id))
            )
            total_count = base_q.count() + 1  # approximate? Instead keep original total
            # Actually total_count should be total without cursor; keep original
            # Recompute total without cursor filter for header
            total_q = db.query(WorkflowExecution)
            if effective_tenant:
                total_q = total_q.filter(WorkflowExecution.tenant_id == effective_tenant)
            if state and state != "all":
                total_q = total_q.filter(WorkflowExecution.status == state)
            total_count = total_q.count()
    rows = base_q.limit(limit + 1).all()  # fetch one extra to detect has_more
    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
        next_cursor = rows[-1].execution_id if rows else None
    else:
        next_cursor = None
    executions = [
        {
            "execution_id": r.execution_id,
            "request_id": r.request_id,
            "tenant_id": r.tenant_id,
            "status": r.status,
            "current_node": r.current_node,
            "created_at": r.created_at.isoformat() if r.created_at else None,
            "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            "trace_id": r.trace_id,
        }
        for r in rows
    ]
    return {
        "count": len(executions),
        "total_count": total_count,
        "page_size": limit,
        "has_more": has_more,
        "next_cursor": next_cursor,
        "executions": executions,
    }


# ---------------------------------------------------------------------------
# Approvals — Fase 5# ---------------------------------------------------------------------------
# Approvals — Fase 5 con snapshot inmutable, expiración, scope_hash y doble aprobación
# ---------------------------------------------------------------------------

@app.get("/v1/procurement/executions/{execution_id}", tags=["procurement"])
def get_execution(execution_id: str, db: Session = Depends(get_db), at: str | None = None):
    # Fase 9 — time-travel: ?at=ISO8601
    if at:
        from procurement_platform.persistence.time_travel import get_execution_at
        snap = get_execution_at(db, execution_id, at)
        if not snap:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "no snapshot at that time"})
        return snap
    # Fase 5: auto-expirar si corresponde al consultar (reanudación durable)
    row = db.get(WorkflowExecution, execution_id)
    # Fase 9 — soft-delete check (hide if tenant soft-deleted unless include_deleted)
    if row:
        try:
            from procurement_platform.persistence.retention import is_tenant_soft_deleted
            if is_tenant_soft_deleted(db, row.tenant_id):
                # For MVP, still return but mark as deleted
                pass
        except Exception:
            pass
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
    format: str | None = None,
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
        has_more = (
            base_q.filter(AuditEventRow.timestamp > rows[-1].timestamp).count() > 0
            or base_q.filter(
                (AuditEventRow.timestamp == rows[-1].timestamp)
                & (AuditEventRow.event_id > rows[-1].event_id)
            ).count()
            > 0
        )
        # simplify: if we got limit rows, assume may have more
        has_more = len(rows) == limit
    next_cursor = rows[-1].event_id if has_more else None
    # F5-3: trace drill-down format
    if format == "trace":
        timeline = []
        for r in rows:
            details = r.details or {}
            timeline.append(
                {
                    "event_id": r.event_id,
                    "event_type": r.event_type,
                    "timestamp": r.timestamp.isoformat(),
                    "trace_id": r.trace_id,
                    "span_id": details.get("span_id") if isinstance(details, dict) else None,
                    "duration_ms": details.get("duration_ms")
                    if isinstance(details, dict)
                    else None,
                    "model_metadata": r.model_metadata,
                    "tool_name": r.tool_name,
                    "actor_id": r.actor_id,
                    "details": details,
                }
            )
        return {
            "execution_id": execution_id,
            "count": len(rows),
            "total": total,
            "trace_id": rows[0].trace_id if rows else None,
            "timeline": timeline,
            "next_cursor": next_cursor,
        }
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
                "span_id": (r.details or {}).get("span_id")
                if isinstance(r.details, dict)
                else None,
                "duration_ms": (r.details or {}).get("duration_ms")
                if isinstance(r.details, dict)
                else None,
                "model_metadata": r.model_metadata,
                "details": r.details,
            }
            for r in rows
        ],
        "next_cursor": next_cursor,
    }


# ---------------------------------------------------------------------------
# Fase 7 — HITL productivo: list, bulk, export, delegation, SLA
# ---------------------------------------------------------------------------
@app.get("/v1/approvals", tags=["approvals"])
def list_approvals(
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
    tenant_id: str | None = None,
    tenant: str | None = None,
    state: str | None = None,
    limit: int = 50,
    cursor: str | None = None,
):
    """Lista aprobaciones con filtro tenant/state — para inbox UI. Paginación cursor por requested_at."""
    # tenant filter: if principal authenticated, restrict to tenant (support alias tenant)
    effective_tenant = tenant_id or tenant or (principal.tenant_id if principal.auth_method != "anonymous" else None)
    rows = db.query(WorkflowExecution).all()
    # build list
    approvals = []
    for row in rows:
        appr = row.approval_request
        if not appr:
            continue
        # tenant isolation
        if effective_tenant and row.tenant_id != effective_tenant:
            continue
        if principal.auth_method != "anonymous" and principal.tenant_id != row.tenant_id:
            continue
        status = appr.get("status")
        if state and state != "all" and status != state:
            continue
        approvals.append(
            {
                "approval_id": appr.get("approval_id"),
                "execution_id": row.execution_id,
                "request_id": row.request_id,
                "tenant_id": row.tenant_id,
                "status": status,
                "scope_hash": appr.get("scope_hash"),
                "total": appr.get("total"),
                "currency": appr.get("currency"),
                "risk_level": appr.get("risk_level"),
                "required_approvals": appr.get("required_approvals", 1),
                "approvals_received": appr.get("approvals_received", 0),
                "approvers": appr.get("approvers", []),
                "requested_by": appr.get("requested_by"),
                "requested_at": appr.get("requested_at"),
                "expires_at": appr.get("expires_at"),
                "escalated_to": appr.get("escalated_to"),
                "escalated_at": appr.get("escalated_at"),
                "execution_status": row.status,
            }
        )
    # sort by requested_at desc (newest first)
    approvals.sort(key=lambda x: x.get("requested_at") or "", reverse=True)
    # cursor pagination (simplified: cursor is approval_id)
    if cursor:
        # find index
        idx = next((i for i, a in enumerate(approvals) if a["approval_id"] == cursor), None)
        if idx is not None:
            approvals = approvals[idx + 1 :]
    total = len(approvals)
    has_more = False
    if len(approvals) > limit:
        has_more = True
        approvals = approvals[:limit]
        next_cursor = approvals[-1]["approval_id"] if approvals else None
    else:
        next_cursor = None
    return {"count": len(approvals), "total": total, "has_more": has_more, "next_cursor": next_cursor, "approvals": approvals}


@app.post("/v1/approvals/bulk/decision", tags=["approvals"])
def bulk_decide(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    """Fase 7 bulk — {approval_ids: [], decision, decided_by, reason} — RBAC admin o approver."""
    approval_ids = payload.get("approval_ids") or payload.get("approvalIds") or []
    decision = payload.get("decision")
    decided_by = payload.get("decided_by")
    reason = payload.get("reason")
    if not approval_ids or not isinstance(approval_ids, list):
        raise HTTPException(status_code=400, detail={"code": "validation_error", "message": "approval_ids required"})
    if decision not in {"approved", "rejected", "needs_changes"}:
        raise HTTPException(status_code=400, detail={"code": "validation_error", "message": "invalid decision"})
    if not decided_by:
        raise HTTPException(status_code=400, detail={"code": "validation_error", "message": "decided_by required"})
    # RBAC: need admin or approver
    if principal.auth_method != "anonymous":
        from procurement_platform.security.rbac import has_role

        if not (has_role(principal, "approver") or has_role(principal, "admin")):
            raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "approver or admin required"})
    trace_id = request.state.trace_id
    results = []
    for aid in approval_ids:
        try:
            # reuse orchestrator logic via service directly to avoid HTTP recursion
            # find execution
            rows = db.query(WorkflowExecution).all()
            target = None
            for row in rows:
                appr = row.approval_request
                if appr and appr.get("approval_id") == aid:
                    target = row
                    break
            if not target:
                results.append({"approval_id": aid, "status": "error", "error": "not_found"})
                continue
            # tenant check
            if principal.auth_method != "anonymous" and principal.tenant_id != target.tenant_id:
                results.append({"approval_id": aid, "status": "error", "error": "tenant_forbidden"})
                continue
            # delegate to orchestrator
            try:
                if decision == "approved":
                    exec_obj = orchestrator.approve_and_complete(db, target.execution_id, decided_by=decided_by, trace_id=trace_id, decision_reason=reason)
                    appr_after = exec_obj.approval_request
                    if appr_after and appr_after.status == ApprovalStatus.pending and appr_after.approvals_received < appr_after.required_approvals:
                        results.append({"approval_id": aid, "status": "partially_approved", "execution_status": exec_obj.status.value})
                    else:
                        results.append({"approval_id": aid, "status": "approved", "execution_status": exec_obj.status.value})
                elif decision == "rejected":
                    exec_obj = orchestrator.reject_execution(db, target.execution_id, decided_by=decided_by, trace_id=trace_id, reason=reason)
                    results.append({"approval_id": aid, "status": "rejected", "execution_status": exec_obj.status.value})
                else:
                    exec_obj = orchestrator.request_changes(db, target.execution_id, decided_by=decided_by, trace_id=trace_id, reason=reason)
                    results.append({"approval_id": aid, "status": "needs_changes", "execution_status": exec_obj.status.value})
            except ValueError as ve:
                msg = str(ve)
                if "expired" in msg:
                    results.append({"approval_id": aid, "status": "error", "error": "expired", "message": msg})
                elif "scope_mismatch" in msg:
                    results.append({"approval_id": aid, "status": "error", "error": "scope_mismatch", "message": msg})
                elif "already" in msg.lower():
                    results.append({"approval_id": aid, "status": "error", "error": "already_decided", "message": msg})
                else:
                    results.append({"approval_id": aid, "status": "error", "error": "validation", "message": msg})
        except Exception as e:
            results.append({"approval_id": aid, "status": "error", "error": str(e)})
    # audit bulk
    try:
        create_audit_event(
            db,
            execution_id="bulk",
            request_id=request.state.request_id,
            event_type="approval.bulk_decided",
            actor_type="human" if principal.auth_method != "anonymous" else "system",
            actor_id=decided_by,
            trace_id=trace_id,
            details={"decision": decision, "count": len(approval_ids), "results": results},
        )
        db.commit()
    except Exception:
        pass
    return {"decision": decision, "decided_by": decided_by, "count": len(approval_ids), "results": results}


@app.get("/v1/approvals/export", tags=["approvals"])
def export_approvals(
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
    tenant_id: str | None = None,
    tenant: str | None = None,
    state: str | None = None,
    format: str = "csv",
):
    """Fase 7 CSV export — ?tenant=&state= — RBAC admin."""
    effective_tenant_param = tenant_id or tenant
    # For export, require admin if principal authenticated, else allow anonymous for local demo
    if principal.auth_method != "anonymous":
        from procurement_platform.security.rbac import has_role

        if not has_role(principal, "admin"):
            # also allow approver to export own tenant
            if not has_role(principal, "approver"):
                raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "admin required for export"})
        effective_tenant = effective_tenant_param or principal.tenant_id
    else:
        effective_tenant = effective_tenant_param

    rows = db.query(WorkflowExecution).all()
    filtered = []
    for row in rows:
        appr = row.approval_request
        if not appr:
            continue
        if effective_tenant and row.tenant_id != effective_tenant:
            continue
        if state and state != "all" and appr.get("status") != state:
            continue
        filtered.append((row, appr))

    if format == "csv":
        from fastapi.responses import PlainTextResponse
        import csv
        import io

        out = io.StringIO()
        writer = csv.writer(out)
        writer.writerow(["approval_id", "execution_id", "request_id", "tenant_id", "status", "total", "currency", "risk_level", "required_approvals", "approvals_received", "requested_at", "expires_at", "escalated_to", "execution_status"])
        for row, appr in filtered:
            writer.writerow(
                [
                    appr.get("approval_id"),
                    row.execution_id,
                    row.request_id,
                    row.tenant_id,
                    appr.get("status"),
                    appr.get("total"),
                    appr.get("currency"),
                    appr.get("risk_level"),
                    appr.get("required_approvals"),
                    appr.get("approvals_received"),
                    appr.get("requested_at"),
                    appr.get("expires_at"),
                    appr.get("escalated_to", ""),
                    row.status,
                ]
            )
        return PlainTextResponse(out.getvalue(), media_type="text/csv", headers={"Content-Disposition": "attachment; filename=approvals.csv"})
    else:
        return {"count": len(filtered), "approvals": [appr for _, appr in filtered]}


@app.post("/v1/approvals/delegation", tags=["approvals"])
def set_delegation_endpoint(
    payload: dict,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    """Fase 7 delegation — {tenant_id, from_user, to_user}."""
    tenant_id = payload.get("tenant_id") or (principal.tenant_id if principal.auth_method != "anonymous" else "tenant_demo")
    from_user = payload.get("from_user") or payload.get("from")
    to_user = payload.get("to_user") or payload.get("to")
    if not from_user or not to_user:
        raise HTTPException(status_code=400, detail={"code": "validation_error", "message": "from_user and to_user required"})
    # RBAC: only admin or from_user themselves can delegate
    if principal.auth_method != "anonymous":
        from procurement_platform.security.rbac import has_role

        if principal.sub != from_user and not has_role(principal, "admin"):
            raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "only admin or delegator can set delegation"})
        if principal.tenant_id != tenant_id:
            raise HTTPException(status_code=403, detail={"code": "tenant_forbidden", "message": "tenant mismatch"})
    from procurement_platform.approvals.service import set_delegation

    set_delegation(tenant_id, from_user, to_user)
    try:
        create_audit_event(
            db,
            execution_id="no_exec",
            request_id=new_id("req"),
            event_type="approval.delegation_set",
            actor_type="human" if principal.auth_method != "anonymous" else "system",
            actor_id=principal.sub if principal.auth_method != "anonymous" else from_user,
            details={"tenant_id": tenant_id, "from": from_user, "to": to_user},
        )
        db.commit()
    except Exception:
        pass
    return {"tenant_id": tenant_id, "from": from_user, "to": to_user, "status": "delegated"}


@app.get("/v1/approvals/delegation", tags=["approvals"])
def get_delegation_endpoint(
    tenant_id: str = "tenant_demo",
    from_user: str | None = None,
    principal: Principal = Depends(get_current_principal),
):
    from procurement_platform.approvals.service import get_delegation, _delegation_store

    if from_user:
        delegate = get_delegation(tenant_id, from_user)
        return {"tenant_id": tenant_id, "from": from_user, "to": delegate}
    # list all for tenant
    with __import__("procurement_platform.approvals.service", fromlist=["_delegation_store"])._delegation_lock:
        result = {f"{k[0]}:{k[1]}": v for k, v in _delegation_store.items() if k[0] == tenant_id}
    return {"tenant_id": tenant_id, "delegations": result}


@app.post("/v1/approvals/sla/check", tags=["approvals"])
def trigger_sla_check(
    payload: dict | None = None,
    request: Request = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    """Fase 7 — trigger SLA check manualmente (job cada 15m via ARQ)."""
    # RBAC: admin or system
    if principal and principal.auth_method != "anonymous":
        from procurement_platform.security.rbac import has_role

        if not (has_role(principal, "admin") or has_role(principal, "approver")):
            raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "admin required"})
    from procurement_platform.approvals.service import check_approval_sla

    trace_id = request.state.trace_id if request else None
    escalated = check_approval_sla(db, trace_id=trace_id)
    return {"escalated_count": len(escalated), "escalated_ids": escalated, "checked_at": utcnow().isoformat()}



# ---------------------------------------------------------------------------
# Fase 8 — Webhooks (svix-style) + Pagination stable
# ---------------------------------------------------------------------------
@app.post("/v1/webhooks/subscriptions", tags=["webhooks"])
def create_webhook_subscription(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    """Crea webhook subscription — {url, secret, events} — HMAC sha256, retry, X-Webhook-Id."""
    tenant_id = payload.get("tenant_id") or (principal.tenant_id if principal.auth_method != "anonymous" else "tenant_demo")
    url = payload.get("url")
    secret = payload.get("secret")
    events = payload.get("events") or ["execution.completed", "approval.requested"]
    if not url:
        raise HTTPException(status_code=400, detail={"code": "validation_error", "message": "url required"})
    if principal.auth_method != "anonymous" and principal.tenant_id != tenant_id:
        raise HTTPException(status_code=403, detail={"code": "tenant_forbidden", "message": "tenant mismatch"})
    # validate events
    allowed = {"execution.completed", "approval.requested", "execution.created", "approval.escalated", "*"}
    for ev in events:
        if ev not in allowed:
            raise HTTPException(status_code=400, detail={"code": "validation_error", "message": f"event {ev} not allowed"})
    from procurement_platform.integrations.webhooks.service import get_webhook_service
    sub = get_webhook_service().create_subscription(tenant_id, url, secret, events)
    # hide secret in response? return with masked
    try:
        create_audit_event(
            db,
            execution_id="no_exec",
            request_id=request.state.request_id,
            event_type="webhook.subscription_created",
            actor_type="human" if principal.auth_method != "anonymous" else "system",
            actor_id=principal.sub if principal.auth_method != "anonymous" else "system",
            trace_id=request.state.trace_id,
            details={"subscription_id": sub["id"], "url": url, "events": events, "tenant_id": tenant_id},
        )
        db.commit()
    except Exception:
        pass
    return sub


@app.get("/v1/webhooks/subscriptions", tags=["webhooks"])
def list_webhook_subscriptions(
    tenant_id: str | None = None,
    tenant: str | None = None,
    principal: Principal = Depends(get_current_principal),
):
    effective_tenant = tenant_id or tenant or (principal.tenant_id if principal.auth_method != "anonymous" else None)
    from procurement_platform.integrations.webhooks.service import get_webhook_service
    subs = get_webhook_service().list_subscriptions(effective_tenant)
    return {"count": len(subs), "subscriptions": subs}


@app.delete("/v1/webhooks/subscriptions/{sub_id}", tags=["webhooks"])
def delete_webhook_subscription(
    sub_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
    tenant_id: str | None = None,
):
    effective_tenant = tenant_id or (principal.tenant_id if principal.auth_method != "anonymous" else "tenant_demo")
    # tenant from subscription must match
    from procurement_platform.integrations.webhooks.service import get_webhook_service
    ok = get_webhook_service().delete_subscription(sub_id, effective_tenant)
    if not ok:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "subscription not found"})
    try:
        create_audit_event(
            db,
            execution_id="no_exec",
            request_id=request.state.request_id,
            event_type="webhook.subscription_deleted",
            actor_type="human" if principal.auth_method != "anonymous" else "system",
            actor_id=principal.sub if principal.auth_method != "anonymous" else "system",
            trace_id=request.state.trace_id,
            details={"subscription_id": sub_id, "tenant_id": effective_tenant},
        )
        db.commit()
    except Exception:
        pass
    return {"status": "deleted", "id": sub_id}


@app.post("/v1/webhooks/test", tags=["webhooks"])
def test_webhook(
    payload: dict,
    request: Request,
    principal: Principal = Depends(get_current_principal),
):
    """Test delivery to a webhook subscription — sends test payload and verifies HMAC."""
    tenant_id = payload.get("tenant_id") or (principal.tenant_id if principal.auth_method != "anonymous" else "tenant_demo")
    url = payload.get("url")
    if not url:
        raise HTTPException(status_code=400, detail={"code": "validation_error", "message": "url required"})
    secret = payload.get("secret") or "test_secret"
    event_type = payload.get("event_type") or "execution.completed"
    test_payload = {"test": True, "tenant_id": tenant_id, "execution_id": "exec_test", "timestamp": utcnow().isoformat()}
    from procurement_platform.integrations.webhooks.service import get_webhook_service
    # create temp sub and deliver
    sub = {"id": "wh_test", "tenant_id": tenant_id, "url": url, "secret": secret, "events": [event_type]}
    result = get_webhook_service()._deliver_to_subscription(sub, event_type, test_payload, max_retries=1)
    return {"delivered": result["success"], "result": result}


@app.get("/v1/approvals/{approval_id}", tags=["approvals"])
def get_approval(
    approval_id: str,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    # buscar por approval_id (scan Fase 1-5)
    rows = db.query(WorkflowExecution).all()
    for row in rows:
        appr = row.approval_request
        if appr and appr.get("approval_id") == approval_id:
            if principal.auth_method != "anonymous" and principal.tenant_id != row.tenant_id:
                raise HTTPException(
                    status_code=403,
                    detail={"code": "tenant_forbidden", "message": "tenant mismatch"},
                )
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
    principal: Principal = Depends(get_current_principal),
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
    # F3-4: RBAC/ABAC — approver must be same tenant and have approver role (if authenticated)
    if principal.auth_method != "anonymous":
        from procurement_platform.security.rbac import has_role

        if principal.tenant_id != target_exec.tenant_id:
            raise HTTPException(
                status_code=403,
                detail={"code": "tenant_forbidden", "message": "approver tenant mismatch"},
            )
        if not has_role(principal, "approver"):
            raise HTTPException(
                status_code=403,
                detail={
                    "code": "forbidden",
                    "message": f"role approver required, has {principal.roles}",
                },
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


# ---------------------------------------------------------------------------
# Fase 9 — Data Platform: time-travel, lineage, flags, retention, tenant deletion
# ---------------------------------------------------------------------------
@app.get("/v1/procurement/executions/{execution_id}/timeline", tags=["procurement"])
def get_execution_timeline(
    execution_id: str,
    db: Session = Depends(get_db),
    at: str | None = None,
):
    """Time-travel timeline: ?at=ISO8601 returns execution state as of that time (Fase 9)."""
    if at:
        from procurement_platform.persistence.time_travel import get_execution_at
        snap = get_execution_at(db, execution_id, at)
        if not snap:
            raise HTTPException(status_code=404, detail={"code": "not_found", "message": "execution not found at that time"})
        return snap
    # without at, return full history
    from procurement_platform.persistence.time_travel import get_execution_history
    history = get_execution_history(db, execution_id)
    if not history and not db.get(WorkflowExecution, execution_id):
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "execution not found"})
    return {"execution_id": execution_id, "history": history, "count": len(history)}


@app.get("/v1/lineage", tags=["procurement"])
def get_lineage(
    db: Session = Depends(get_db),
    document_id: str | None = None,
    policy_id: str | None = None,
    supplier_id: str | None = None,
    execution_id: str | None = None,
):
    """Lineage query: execution → doc → policy → supplier (Fase 9).
    Ejemplo: GET /v1/lineage?document_id=policy_budget_v1 → ejecuciones afectadas.
    """
    from procurement_platform.persistence.lineage import (
        get_executions_for_document,
        get_executions_for_policy,
        get_executions_for_supplier,
        get_lineage_for_execution,
    )
    if document_id:
        execs = get_executions_for_document(db, document_id)
        return {"document_id": document_id, "executions": execs, "count": len(execs)}
    if policy_id:
        execs = get_executions_for_policy(db, policy_id)
        return {"policy_id": policy_id, "executions": execs, "count": len(execs)}
    if supplier_id:
        execs = get_executions_for_supplier(db, supplier_id)
        return {"supplier_id": supplier_id, "executions": execs, "count": len(execs)}
    if execution_id:
        lineage = get_lineage_for_execution(db, execution_id)
        return lineage
    raise HTTPException(status_code=400, detail={"code": "validation_error", "message": "one of document_id, policy_id, supplier_id, execution_id required"})


@app.get("/v1/flags", tags=["ops"])
def list_flags():
    from procurement_platform.infra.feature_flags import get_flag_provider
    provider = get_flag_provider()
    return {"flags": provider.get_all()}


@app.get("/v1/flags/{flag_name}", tags=["ops"])
def get_flag(flag_name: str, tenant_id: str | None = None):
    from procurement_platform.infra.feature_flags import get_flag_provider
    provider = get_flag_provider()
    enabled = provider.is_enabled(flag_name, tenant_id)
    return {"flag": flag_name, "enabled": enabled, "tenant_id": tenant_id}


@app.post("/v1/retention/run", tags=["ops"])
def run_retention_endpoint(
    payload: dict | None = None,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    """Trigger retention job (admin). Query param ?dry_run=true para simular."""
    # RBAC: admin only
    if principal and principal.auth_method != "anonymous":
        from procurement_platform.security.rbac import has_role
        if not has_role(principal, "admin"):
            raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "admin required"})
    from procurement_platform.persistence.retention import run_retention
    dry_run = False
    if payload and payload.get("dry_run"):
        dry_run = True
    result = run_retention(db, dry_run=dry_run)
    return result


@app.delete("/v1/tenants/{tenant_id}/data", tags=["ops"])
def delete_tenant_data(
    tenant_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    """GDPR soft-delete: DELETE /v1/tenants/{id}/data — tombstone, oculta de queries normales."""
    # RBAC: tenant isolation + admin or tenant owner
    if principal and principal.auth_method != "anonymous":
        from procurement_platform.security.rbac import has_role
        if principal.tenant_id != tenant_id and not has_role(principal, "admin"):
            raise HTTPException(status_code=403, detail={"code": "tenant_forbidden", "message": "tenant mismatch"})
    # Check if already soft deleted
    from procurement_platform.persistence.retention import is_tenant_soft_deleted, soft_delete_tenant
    if is_tenant_soft_deleted(db, tenant_id):
        return {"tenant_id": tenant_id, "status": "already_deleted"}
    result = soft_delete_tenant(db, tenant_id, actor_id=principal.sub if principal and principal.auth_method != "anonymous" else "system")
    return result


@app.post("/v1/bq/drain", tags=["ops"])
def trigger_bq_drain(
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
    batch: int = 50,
):
    """Trigger BigQuery drainer manually (for tests/staging)."""
    if principal and principal.auth_method != "anonymous":
        from procurement_platform.security.rbac import has_role
        if not has_role(principal, "admin"):
            raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "admin required"})
    from procurement_platform.pipeline.bq_drainer import drain_to_bigquery
    result = drain_to_bigquery(db, batch=batch)
    return result


@app.get("/v1/bq/query", tags=["ops"])
def query_bq_fake(
    dataset: str = "procurement_ops",
    table: str = "bq_audit",
    execution_id: str | None = None,
):
    """Fake BigQuery query for local dev: SELECT * FROM ops.audit WHERE execution_id=..."""
    from procurement_platform.pipeline.bq_drainer import query_fake_bq
    rows = query_fake_bq(dataset, table, execution_id)
    return {"dataset": dataset, "table": table, "execution_id": execution_id, "count": len(rows), "rows": rows[:10]}


@app.get("/v1/artifacts", tags=["ops"])
def list_artifacts(prefix: str = ""):
    from procurement_platform.infra.gcs import get_artifact_store
    store = get_artifact_store()
    keys = store.list(prefix)
    return {"prefix": prefix, "count": len(keys), "keys": keys[:20]}


@app.get("/v1/procurement/executions/{execution_id}/time-travel", tags=["procurement"])
def time_travel_execution(
    execution_id: str,
    at: str,
    db: Session = Depends(get_db),
):
    """Alias for timeline time-travel: GET /v1/procurement/executions/{id}/time-travel?at=ISO"""
    from procurement_platform.persistence.time_travel import get_execution_at
    snap = get_execution_at(db, execution_id, at)
    if not snap:
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": "no snapshot at that time"})
    return snap


@app.post("/v1/documents", tags=["documents"])
def upload_document(payload: dict, request: Request, db: Session = Depends(get_db)):
    """Ingesta RAG Fase 3 + F4-6 GCS: valida, clasifica, detecta injection, embeddings, version history."""
    from datetime import UTC, datetime

    from procurement_platform.rag.models import Document, DocumentMetadata

    gcs_uri = payload.get("gcs_uri")
    # F4-6: si viene gcs_uri sin content, delegar a GCSIngestor
    if gcs_uri and not payload.get("content"):
        # requiere al menos tenant y gcs_uri
        tenant_id = payload.get("tenant_id", "tenant_demo")
        title = payload.get("title", f"GCS {gcs_uri}")

        # construir metadata_kwargs para GCS
        def parse_dt(v):
            if not v:
                return None
            try:
                return datetime.fromisoformat(v.replace("Z", "+00:00"))
            except Exception:
                return None

        metadata_kwargs = {
            "document_id": payload.get("document_id")
            or gcs_uri.replace("gs://", "").replace("/", "_")[:64],
            "tenant_id": tenant_id,
            "title": title,
            "doc_type": payload.get("doc_type", "policy"),
            "classification": payload.get("classification", "internal"),
            "jurisdiction": payload.get("jurisdiction", "global"),
            "location_id": payload.get("location_id"),
            "version": payload.get("version", "1.0.0"),
            "valid_from": parse_dt(payload.get("valid_from")) or datetime.now(UTC),
            "valid_to": parse_dt(payload.get("valid_to")),
            "status": payload.get("status", "approved"),
            "allowed_tenants": payload.get("allowed_tenants", [tenant_id]),
        }
        rag = get_rag_service()
        try:
            status, chunks = rag.ingest_from_gcs(
                gcs_uri=gcs_uri,
                metadata_kwargs=metadata_kwargs,
                db=db,
                allow_reindex=bool(payload.get("allow_reindex", False)),
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
                    "document_id": metadata_kwargs["document_id"],
                    "chunks": len(chunks),
                    "status": status,
                    "gcs_uri": gcs_uri,
                    "version": metadata_kwargs["version"],
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
            # fetch updated doc hash if available
            from procurement_platform.persistence.models import DocumentRow

            doc_row = db.get(DocumentRow, metadata_kwargs["document_id"]) if db else None
            content_hash = doc_row.content_hash if doc_row and doc_row.content_hash else None
            sec_flags = doc_row.security_flags if doc_row and doc_row.security_flags else []
            is_mal = bool(doc_row.is_malicious) if doc_row else False
            return JSONResponse(
                status_code=http_status,
                content={
                    "status": status,
                    "document_id": metadata_kwargs["document_id"],
                    "chunks_created": len(chunks),
                    "content_hash": content_hash,
                    "security_flags": sec_flags,
                    "is_malicious": is_mal,
                    "gcs_uri": gcs_uri,
                    "version": metadata_kwargs["version"],
                    "request_id": request.state.request_id,
                },
            )
        except Exception as e:
            raise HTTPException(
                status_code=422, detail={"code": "gcs_ingest_failed", "message": str(e)}
            )

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
    # attach gcs_uri if present alongside content (F4-6)
    if gcs_uri:
        document.__dict__["_gcs_uri"] = gcs_uri  # type: ignore

    rag = get_rag_service()
    # F4-6: allow_reindex if version bump
    allow_reindex = bool(payload.get("allow_reindex", False))
    status, chunks = rag.ingest_document(
        document=document,
        filename=payload.get("filename"),
        actor_id=request.state.request_id,
        db=db,
    )
    # if duplicate but allow_reindex True, retry with flag
    if status == "duplicate" and allow_reindex:
        # re-invoke pipeline with allow_reindex

        # use same rag pipeline but force
        # we simulate by clearing dedup for this doc hash and retrying
        # fallback: call pipeline directly with allow_reindex
        try:
            # clear specific hash to allow reindex
            rag.pipeline._seen_hashes.discard(document.metadata.content_hash or "")  # type: ignore
            # also need to handle gcs path: retry
            status, chunks = rag.pipeline.ingest(
                document=document, filename=payload.get("filename"), allow_reindex=True
            )
            if status == "indexed":
                rag.retrieval.index_chunks(chunks)
                rag._persist_chunks(db, document, chunks)  # type: ignore
                rag._ingestion_log.append(  # type: ignore
                    {
                        "document_id": document.metadata.document_id,
                        "status": status,
                        "chunks": len(chunks),
                        "hash": document.metadata.content_hash,
                        "flags": document.metadata.security_flags,
                        "actor_id": request.state.request_id,
                        "timestamp": datetime.now(UTC).isoformat(),
                        "reindexed": True,
                    }
                )
        except Exception:
            pass

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
            "gcs_uri": gcs_uri,
            "version": version,
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
            "gcs_uri": gcs_uri,
            "version": version,
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
    use_reranker: bool | None = None,
    db: Session = Depends(get_db),
):
    """Endpoint de debug para RAG — Fase 3 (no expone documentos fuera de permiso).

    F4-4: soporta ?use_reranker=true para re-ranking cross-encoder.
    """
    rag = get_rag_service()
    res = rag.retrieve(
        query=query,
        tenant_id=tenant_id,
        location_id=location_id,
        jurisdiction=jurisdiction,
        top_k=top_k,
        use_reranker=use_reranker,
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
        "reranked": res.get("reranked", False),
    }


@app.post("/v1/rag/feedback", tags=["rag"])
def rag_feedback(
    payload: dict,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    """F4-5: feedback loop thumbs up/down — {chunk_id, useful, tenant_id?}."""
    chunk_id = payload.get("chunk_id")
    useful = payload.get("useful")
    tenant_id = payload.get("tenant_id") or principal.tenant_id
    if not chunk_id or useful is None:
        raise HTTPException(
            status_code=400,
            detail={"code": "validation_error", "message": "chunk_id and useful required"},
        )
    if not isinstance(useful, bool):
        # permitir "true"/"false" strings
        if isinstance(useful, str):
            useful = useful.lower() in ("true", "1", "yes", "up")
        else:
            useful = bool(useful)
    # tenant check: if principal authenticated, enforce tenant match vs row tenant
    try:
        from procurement_platform.rag.feedback_store import record_feedback

        result = record_feedback(
            db,
            chunk_id=chunk_id,
            useful=useful,
            actor_id=principal.sub,
            tenant_id=tenant_id if principal.auth_method != "anonymous" else None,
        )
    except ValueError as e:
        if "tenant mismatch" in str(e):
            raise HTTPException(
                status_code=403, detail={"code": "tenant_forbidden", "message": str(e)}
            )
        raise HTTPException(status_code=404, detail={"code": "not_found", "message": str(e)})
    except Exception as e:
        raise HTTPException(
            status_code=404,
            detail={"code": "not_found", "message": f"chunk {chunk_id} not found: {e}"},
        )
    # audit
    try:
        create_audit_event(
            db,
            execution_id="no_exec",
            request_id=request.state.request_id,
            event_type="rag.feedback.recorded",
            actor_type="human" if principal.auth_method != "anonymous" else "system",
            actor_id=principal.sub,
            trace_id=request.state.trace_id,
            details={
                "chunk_id": chunk_id,
                "useful": useful,
                "feedback_score": result["feedback_score"],
            },
        )
        db.commit()
    except Exception:
        pass
    return result


@app.get("/v1/rag/feedback", tags=["rag"])
def rag_feedback_stats(
    chunk_id: str | None = None,
    tenant_id: str = "tenant_demo",
    limit: int = 10,
    db: Session = Depends(get_db),
):
    """Lista feedback stats por tenant o por chunk."""
    if chunk_id:
        from procurement_platform.rag.feedback_store import get_feedback_stats

        stats = get_feedback_stats(db, chunk_id)
        if not stats:
            raise HTTPException(
                status_code=404, detail={"code": "not_found", "message": "chunk not found"}
            )
        return stats
    from procurement_platform.rag.feedback_store import list_top_feedback

    return {"tenant_id": tenant_id, "results": list_top_feedback(db, tenant_id, limit=limit)}


# ---------------------------------------------------------------------------
# Fase 10 — Secrets rotation + workload identity (no key file)
# ---------------------------------------------------------------------------
@app.post("/v1/secrets/{secret_id}/rotate", tags=["ops"])
def rotate_secret(
    secret_id: str,
    request: Request,
    db: Session = Depends(get_db),
    principal: Principal = Depends(get_current_principal),
):
    """Manual rotation trigger (admin) — emite audit secret.rotation con workload_identity."""
    # RBAC: admin only
    if principal and principal.auth_method != "anonymous":
        from procurement_platform.security.rbac import has_role

        if not has_role(principal, "admin"):
            raise HTTPException(status_code=403, detail={"code": "forbidden", "message": "admin required"})
    from procurement_platform.security.secrets_rotation import emit_secret_rotation_audit

    result = emit_secret_rotation_audit(
        db, secret_id=secret_id, actor_id=principal.sub if principal.auth_method != "anonymous" else "system", trace_id=request.state.trace_id
    )
    return result


@app.get("/v1/secrets/rotation/status", tags=["ops"])
def secrets_status():
    """Verifica workload identity (no key file) y rotation config."""
    from procurement_platform.security.secrets_rotation import is_workload_identity_enabled

    return {
        "workload_identity": is_workload_identity_enabled(),
        "rotation_days": 30,
        "key_file_used": not is_workload_identity_enabled(),
        "message": "Workload Identity (no key file) enabled" if is_workload_identity_enabled() else "Key file detected — should use WIF",
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
