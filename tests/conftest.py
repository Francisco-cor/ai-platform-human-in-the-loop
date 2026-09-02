import os

# Force test env
os.environ["PROCUREMENT_APP_ENV"] = "ci"
os.environ["PROCUREMENT_DATABASE_URL"] = "sqlite:///./test.db"
os.environ["PROCUREMENT_REDIS_URL"] = "redis://localhost:6379/1"
os.environ["AGENT_STATION_CALLBACK_ENABLED"] = "false"
os.environ["PROCUREMENT_LLM_PROVIDER"] = "fake"
os.environ["PROCUREMENT_LLM_FALLBACK_ENABLED"] = "true"
os.environ["GEMINI_API_KEY"] = ""
os.environ["DEEPSEEK_API_KEY"] = ""

import pytest
from fastapi.testclient import TestClient

from procurement_platform.config.settings import reset_settings_cache
from procurement_platform.persistence.database import (
    Base,
    get_engine,
    get_sessionmaker,
    reset_engine_cache,
)


@pytest.fixture(scope="session", autouse=True)
def setup_test_db():
    reset_settings_cache()
    reset_engine_cache()
    # ensure clean db file — handle Windows file lock
    if os.path.exists("./test.db"):
        try:
            os.remove("./test.db")
        except PermissionError:
            # file locked by previous run; try dispose and retry
            try:
                # attempt to dispose any existing engine and retry
                from procurement_platform.persistence.database import get_engine as _ge

                try:
                    _ge().dispose()
                except Exception:
                    pass
                reset_engine_cache()
                import time as _time
                import gc as _gc

                _gc.collect()
                _time.sleep(0.1)
                if os.path.exists("./test.db"):
                    os.remove("./test.db")
            except Exception:
                # fallback: try to clear via SQL if file remains
                pass
    engine = get_engine()
    # import models
    import procurement_platform.persistence.models  # noqa: F401

    # ensure tables exist even if file not removed (drop+create for isolation)
    try:
        Base.metadata.drop_all(bind=engine)
    except Exception:
        pass
    Base.metadata.create_all(bind=engine)
    yield
    # teardown
    try:
        engine.dispose()
    except Exception:
        pass
    reset_engine_cache()
    if os.path.exists("./test.db"):
        try:
            os.remove("./test.db")
        except Exception:
            pass


@pytest.fixture()
def db_session():
    SessionLocal = get_sessionmaker()
    session = SessionLocal()
    try:
        yield session
    finally:
        session.rollback()
        session.close()


@pytest.fixture(autouse=True)
def _reset_gateway_global():
    # Fase 5: limpiar idempotency global entre tests para evitar contaminación
    try:
        from procurement_platform.tools.gateway import _GLOBAL_CALL_LOG, _GLOBAL_IDEMPOTENCY

        _GLOBAL_IDEMPOTENCY.clear()
        _GLOBAL_CALL_LOG.clear()
    except Exception:
        pass
    try:
        from procurement_platform.workflows.orchestrator import _execution_locks

        _execution_locks.clear()
    except Exception:
        pass
    # Fase 7: rate limiter
    try:
        from procurement_platform.security.rate_limiter import reset_rate_limiter

        reset_rate_limiter()
    except Exception:
        pass
    # Fase 7: clear RAG service
    try:
        from procurement_platform.workflows.orchestrator import get_rag_service

        svc = get_rag_service()
        if svc:
            svc.clear()
    except Exception:
        pass
    # F1-3: reset lock manager
    try:
        from procurement_platform.infra.locks.manager import reset_lock_manager

        reset_lock_manager()
    except Exception:
        pass
    # F2-5: clear outbox between tests (prevent accumulation)
    try:
        from procurement_platform.persistence.database import get_sessionmaker
        from procurement_platform.persistence.models import OutboxEvent

        SessionLocal = get_sessionmaker()
        _db = SessionLocal()
        _db.query(OutboxEvent).delete()
        _db.commit()
        _db.close()
    except Exception:
        pass
    # F5-2: reset metrics
    try:
        from procurement_platform.observability.metrics import reset_metrics

        reset_metrics()
    except Exception:
        pass
    # F5-4: reset finops
    try:
        from procurement_platform.workflows.orchestrator import reset_finops_state

        reset_finops_state()
    except Exception:
        pass
    # F5-1: reset tracing contextvars
    try:
        from procurement_platform.observability.logging import (
            request_id_ctx,
            span_id_ctx,
            trace_id_ctx,
        )

        for ctx in (request_id_ctx, span_id_ctx, trace_id_ctx):
            try:
                ctx.set(None)
            except Exception:
                pass
    except Exception:
        pass
    # Fase 6: reset prompt cache, llm cache, tenant config
    try:
        from procurement_platform.agents.prompts import reset_prompt_cache

        reset_prompt_cache()
    except Exception:
        pass
    try:
        from procurement_platform.agents.cache import reset_llm_cache

        reset_llm_cache()
    except Exception:
        pass
    try:
        import os as _os

        _os.environ.pop("PROCUREMENT_TENANT_LLM_CONFIG", None)
        _os.environ.pop("PROCUREMENT_PROMPT_VERSION", None)
        from procurement_platform.config.settings import reset_settings_cache as _rsc

        # do not clear globally if test sets env explicitly; will be handled per test
        pass
    except Exception:
        pass
    # Fase 7: reset notifier, delegations, SLA
    try:
        from procurement_platform.notifications.service import reset_notifier

        reset_notifier()
    except Exception:
        pass
    try:
        from procurement_platform.approvals.service import clear_delegations

        clear_delegations()
    except Exception:
        pass
    # Fase 9: clear BQ fake, artifact store, tombstones, flags, webhooks
    try:
        from procurement_platform.pipeline.bq_drainer import clear_fake_bq

        clear_fake_bq()
    except Exception:
        pass
    try:
        from procurement_platform.infra.gcs import reset_artifact_store

        reset_artifact_store()
    except Exception:
        pass
    try:
        from procurement_platform.persistence.retention import clear_tombstones

        clear_tombstones()
        from procurement_platform.persistence.database import get_sessionmaker
        from procurement_platform.persistence.models import AuditEventRow

        SessionLocal = get_sessionmaker()
        _db = SessionLocal()
        _db.query(AuditEventRow).filter(AuditEventRow.event_type == "tenant.data_soft_deleted").delete()
        _db.commit()
        _db.close()
    except Exception:
        pass
    try:
        from procurement_platform.infra.feature_flags import reset_flag_provider

        reset_flag_provider()
    except Exception:
        pass
    try:
        from procurement_platform.integrations.webhooks.service import reset_webhook_service

        reset_webhook_service()
    except Exception:
        pass
    try:
        import os as _os2

        _os2.environ.pop("PROCUREMENT_GCS_BUCKET", None)
        _os2.environ.pop("PROCUREMENT_BIGQUERY_DATASET", None)
        _os2.environ.pop("PROCUREMENT_RETENTION_DAYS", None)
    except Exception:
        pass
    yield
    try:
        from procurement_platform.infra.locks.manager import reset_lock_manager

        reset_lock_manager()
    except Exception:
        pass
    try:
        from procurement_platform.agents.cache import reset_llm_cache

        reset_llm_cache()
    except Exception:
        pass
    try:
        from procurement_platform.agents.prompts import reset_prompt_cache

        reset_prompt_cache()
    except Exception:
        pass
    try:
        from procurement_platform.notifications.service import reset_notifier

        reset_notifier()
    except Exception:
        pass
    try:
        from procurement_platform.approvals.service import clear_delegations

        clear_delegations()
    except Exception:
        pass
    try:
        from procurement_platform.pipeline.bq_drainer import clear_fake_bq

        clear_fake_bq()
    except Exception:
        pass
    try:
        from procurement_platform.infra.gcs import reset_artifact_store

        reset_artifact_store()
    except Exception:
        pass
    try:
        from procurement_platform.persistence.retention import clear_tombstones

        clear_tombstones()
    except Exception:
        pass
    try:
        from procurement_platform.infra.feature_flags import reset_flag_provider

        reset_flag_provider()
    except Exception:
        pass
    try:
        from procurement_platform.integrations.webhooks.service import reset_webhook_service

        reset_webhook_service()
    except Exception:
        pass


@pytest.fixture()
def client():
    from procurement_platform.api.main import app
    from procurement_platform.persistence.database import get_db

    # override get_db to use test engine session
    SessionLocal = get_sessionmaker()

    def override_get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()
