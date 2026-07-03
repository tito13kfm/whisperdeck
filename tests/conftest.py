"""Shared pytest fixtures: an isolated per-test database and an
authenticated TestClient against the real FastAPI app."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import app as app_module
from database import init_db


@pytest.fixture()
def db_session(tmp_path):
    """A SQLAlchemy session bound to a fresh sqlite file per test, built
    through the same init_db() path the real app uses (so schema/migration
    logic is exercised identically to production)."""
    db_path = tmp_path / "test.db"
    engine, SessionLocal, _ = init_db(str(db_path))
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


@pytest.fixture()
def client(db_session):
    """TestClient wired to db_session via a get_db override, with a
    logged-in test user (cookies carried automatically by TestClient).

    Does NOT use `with TestClient(app) as client:` — that would trigger
    the app's lifespan and start queue_worker_loop against the real
    production database, which we don't want touched by tests."""
    def _override_get_db():
        yield db_session

    app_module.app.dependency_overrides[app_module.get_db] = _override_get_db
    test_client = TestClient(app_module.app)
    test_client.post(
        "/api/register",
        json={"username": "testuser", "password": "testpass123"},
    )
    yield test_client
    app_module.app.dependency_overrides.clear()
