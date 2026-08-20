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
    # SQLite needs check_same_thread=False
    connect_args = {}
    if url.startswith("sqlite"):
        connect_args = {"check_same_thread": False}
    engine = create_engine(url, echo=echo, connect_args=connect_args, future=True)
    return engine


_engine = None
_SessionLocal = None


def get_sessionmaker():
    global _engine, _SessionLocal
    if _SessionLocal is None:
        _engine = get_engine()
        _SessionLocal = sessionmaker(bind=_engine, autoflush=False, autocommit=False, future=True)
    return _SessionLocal


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
