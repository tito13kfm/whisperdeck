"""Shared pytest fixtures: an isolated per-test database and an
authenticated TestClient against the real FastAPI app."""
import os
import sys

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def pytest_configure(config):
    """Fail fast with a clear message if the suite is run under the wrong
    interpreter. `librosa` is a required base dependency (requirements.txt,
    not an optional extra) — it's the always-available voice-ID fallback
    backend. If it's missing, this isn't running under this project's
    `.venv`, and the real symptom is a handful of confusing, unrelated-looking
    failures in test_voice_id.py/test_voice_match_job.py rather than an
    obvious "wrong interpreter" error. Catch that here instead."""
    try:
        import librosa  # noqa: F401
    except ImportError:
        import pytest as _pytest
        _pytest.exit(
            "\nWrong Python interpreter: 'librosa' is not importable.\n"
            "Run tests with this project's virtualenv, not a bare `python`/`pytest` on PATH:\n"
            "    .venv\\Scripts\\python.exe -m pytest\n"
            "(A bare `python` on PATH may resolve to a system install without this "
            "project's dependencies, which otherwise produces misleading failures in "
            "test_voice_id.py / test_voice_match_job.py instead of a clear error.)\n",
            returncode=1,
        )


import app as app_module
from database import init_db
from services.security import rate_limiter


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
    # rate_limiter is a process-wide singleton (services/security.py). Under
    # starlette's TestClient, request.client.host is always the constant
    # "testclient" — never None — so app.py's "skip rate limiting when there's
    # no real client IP" check does NOT exempt tests. Without this reset, the
    # 5-requests/5-minutes register bucket fills after the 5th test in the
    # whole pytest session and every later test's register call 429s, leaving
    # its client unauthenticated and causing unrelated-looking 401s downstream.
    rate_limiter._buckets.clear()
    test_client = TestClient(app_module.app)
    test_client.post(
        "/api/register",
        json={"username": "testuser", "password": "testpass123"},
    )
    # PUT/POST/PATCH/DELETE mutations require X-CSRF-Token (services/security.py,
    # added in #22). Set it once as a default header so every test's mutation
    # calls carry it without each test having to fetch/attach it itself.
    csrf_token = test_client.get("/api/csrf-token").json()["token"]
    test_client.headers["X-CSRF-Token"] = csrf_token
    yield test_client
    app_module.app.dependency_overrides.clear()
