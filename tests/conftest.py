import os

# Force test env
os.environ["PROCUREMENT_APP_ENV"] = "ci"
os.environ["PROCUREMENT_DATABASE_URL"] = "sqlite:///./test.db"
os.environ["PROCUREMENT_REDIS_URL"] = "redis://localhost:6379/1"
os.environ["AGENT_STATION_CALLBACK_ENABLED"] = "false"

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
    # ensure clean db file
    if os.path.exists("./test.db"):
        os.remove("./test.db")
    engine = get_engine()
    # import models
    import procurement_platform.persistence.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    yield
    # teardown
    engine.dispose()
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
