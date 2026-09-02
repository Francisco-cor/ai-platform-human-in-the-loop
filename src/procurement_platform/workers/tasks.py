"""ARQ worker tasks — F2-2 skeleton.

Enqueue sync fallback if async disabled or redis unavailable.
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)


async def run_workflow(ctx, execution_id: str, trace_id: str | None = None) -> dict:
    """ARQ task: advance workflow for execution_id.

    Called by worker; ctx is ARQ context with redis pool.
    """
    from procurement_platform.config.settings import get_settings
    from procurement_platform.persistence.database import get_sessionmaker
    from procurement_platform.workflows.orchestrator import WorkflowOrchestrator

    settings = get_settings()
    logger.info("run_workflow start", extra={"execution_id": execution_id, "trace_id": trace_id})
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        orch = WorkflowOrchestrator()
        # advance until AWAITING or BLOCKED (same as sync path)
        result = orch.advance_synthetic(db, execution_id, trace_id=trace_id)
        status = result.status.value if hasattr(result, "status") else str(result)
        logger.info("run_workflow done", extra={"execution_id": execution_id, "status": status})
        return {"execution_id": execution_id, "status": status}
    except Exception as e:
        logger.exception(
            "run_workflow failed", extra={"execution_id": execution_id, "error": str(e)}
        )
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass


# Alias for direct sync execution (tests, fallback)
def run_workflow_sync(execution_id: str, trace_id: str | None = None) -> dict:
    """Synchronous wrapper for tests when async disabled."""
    import asyncio

    try:
        return asyncio.run(run_workflow({}, execution_id, trace_id))  # type: ignore[arg-type]
    except RuntimeError:
        # already in loop
        import concurrent.futures

        with concurrent.futures.ThreadPoolExecutor() as pool:
            fut = pool.submit(asyncio.run, run_workflow({}, execution_id, trace_id))  # type: ignore[arg-type]
            return fut.result()


async def check_approval_sla_job(ctx) -> dict:
    """Fase 7 — job cada 15m (ARQ) — escanea aprobaciones pending >12h y escala."""
    from procurement_platform.approvals.service import check_approval_sla
    from procurement_platform.persistence.database import get_sessionmaker

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        escalated = check_approval_sla(db, trace_id=None)
        logger.info("check_approval_sla_job done", extra={"escalated": escalated})
        return {"escalated": escalated, "count": len(escalated)}
    except Exception as e:
        logger.exception("check_approval_sla_job failed", extra={"error": str(e)})
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass


def enqueue_workflow(execution_id: str, trace_id: str | None = None) -> bool:
    """Try to enqueue run_workflow via ARQ/Redis; return True if enqueued, False if fallback to sync.

    Fallback to sync is intentional for ci/local without redis.
    """
    from procurement_platform.config.settings import get_settings

    settings = get_settings()
    if not settings.async_enabled:
        return False
    # try redis lpush as lightweight queue (without ARQ dependency)
    try:
        import redis  # type: ignore
        import json

        r = redis.from_url(settings.redis_url, socket_connect_timeout=0.2, socket_timeout=0.2)
        payload = json.dumps({"execution_id": execution_id, "trace_id": trace_id})
        r.lpush("procurement:jobs", payload)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------------------
# Fase 9 — Data Platform jobs (Bq drainer batch 10s, retention daily)
# ---------------------------------------------------------------------------

async def drain_bq_job(ctx, batch: int = 50) -> dict:
    """Fase 9 — ARQ job cada 10s: drena outbox_events a BigQuery (fake en dev)."""
    from procurement_platform.persistence.database import get_sessionmaker
    from procurement_platform.pipeline.bq_drainer import drain_to_bigquery

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        result = drain_to_bigquery(db, batch=batch)
        logger.info("drain_bq_job done", extra={"result": result})
        return result
    except Exception as e:
        logger.exception("drain_bq_job failed", extra={"error": str(e)})
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass


async def drain_outbox_job(ctx, batch: int = 50) -> dict:
    """Alias para drain_bq_job — compatibilidad F2 outbox drainer."""
    return await drain_bq_job(ctx, batch=batch)


async def retention_job(ctx, retention_days: int | None = None, dry_run: bool = False) -> dict:
    """Fase 9 — job diario retención: borra audit_events > retention_days pero mantiene hashes."""
    from procurement_platform.persistence.database import get_sessionmaker
    from procurement_platform.persistence.retention import run_retention

    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        result = run_retention(db, retention_days=retention_days, dry_run=dry_run)
        logger.info("retention_job done", extra={"result": result})
        return result
    except Exception as e:
        logger.exception("retention_job failed", extra={"error": str(e)})
        raise
    finally:
        try:
            db.close()
        except Exception:
            pass
