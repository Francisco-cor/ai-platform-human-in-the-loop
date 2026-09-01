"""Database engine and session factory (Fase 1)."""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker

from procurement_platform.config.settings import get_settings


class Base(DeclarativeBase):
    pass


def get_engine(echo: bool = False):
    settings = get_settings()
    url = settings.database_url
    connect_args: dict = {}
    engine_kwargs: dict = {"echo": echo, "future": True, "pool_pre_ping": True}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
        # small timeout for SQLite busy handling (Windows lock mitigation)
        engine_kwargs["connect_args"] = connect_args
        # SQLite: no pooling, use NullPool implicitly via default for memory
        # keep autocommit handling simple
    else:
        # Postgres / production: pooling + statement timeout
        # statement_timeout 5s prevents long running queries from blocking
        connect_args = {"options": "-c statement_timeout=5000"}
        engine_kwargs["connect_args"] = connect_args
        engine_kwargs["pool_size"] = 10
        engine_kwargs["max_overflow"] = 10
        engine_kwargs["pool_recycle"] = 3600
        engine_kwargs["pool_timeout"] = 10
    engine = create_engine(url, **engine_kwargs)
    return engine


_engine = None
_SessionLocal = None


def get_sessionmaker():
    global _engine, _SessionLocal
    if _SessionLocal is None:
        _engine = get_engine()
        _SessionLocal = sessionmaker(
            bind=_engine, autoflush=False, autocommit=False, future=True, expire_on_commit=False
        )
    return _SessionLocal


def check_db_connection() -> dict[str, str]:
    """Health helper for /readyz — returns status dict."""
    try:
        eng = get_engine()
        with eng.connect() as conn:
            conn.execute(__import__("sqlalchemy").text("SELECT 1"))
        return {"status": "ok"}
    except Exception as e:
        return {"status": "error", "error": str(e)[:200]}


def get_db():
    SessionLocal = get_sessionmaker()
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def init_db() -> None:
    # import models to register
    import procurement_platform.persistence.models  # noqa: F401

    engine = get_engine()
    Base.metadata.create_all(bind=engine)


def reset_engine_cache() -> None:
    global _engine, _SessionLocal
    _engine = None
    _SessionLocal = None
