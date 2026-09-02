"""ARQ settings — F2-2.

Requires `arq` installed for production worker: pip install arq
"""

from __future__ import annotations

try:
    from arq.connections import RedisSettings

    def get_arq_redis_settings():
        from procurement_platform.config.settings import get_settings

        settings = get_settings()
        # parse redis_url into host/port/db
        # fallback to localhost if not set
        url = settings.redis_url
        # arq expects host, port, database
        # simple parse
        import urllib.parse

        parsed = urllib.parse.urlparse(url)
        return RedisSettings(
            host=parsed.hostname or "localhost",
            port=parsed.port or 6379,
            database=int(parsed.path.lstrip("/") or 0),
        )

    ARQ_REDIS_SETTINGS = get_arq_redis_settings()

    # Functions to be registered as ARQ tasks
    from procurement_platform.workers.tasks import check_approval_sla_job, run_workflow

    try:
        from arq import cron

        _cron_jobs = [cron(check_approval_sla_job, minute={0, 15, 30, 45}, run_at_startup=False)]  # cada 15m
    except Exception:
        _cron_jobs = []

    class WorkerSettings:
        functions = [run_workflow, check_approval_sla_job]
        cron_jobs = _cron_jobs
        redis_settings = ARQ_REDIS_SETTINGS
        max_jobs = 10
        job_timeout = 60  # seconds per workflow advance

except ImportError:
    # arq not installed in ci (optional dep) — keep import safe
    WorkerSettings = None  # type: ignore
    ARQ_REDIS_SETTINGS = None  # type: ignore
