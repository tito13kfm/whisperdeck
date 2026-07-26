"""Tests for /api/bootstrap — the one-shot boot payload that replaces the
4-5 sequential requests the frontend used to make on first paint (issue
#143). Covers the unauthenticated path, the authenticated path, and the
mirror guarantee: the embedded `status`, `recent_transcripts`, and `jobs`
fields must be byte-equivalent to calling /api/status, /api/transcripts,
and /api/jobs individually — drift here would render different data on
first paint than after a polling refresh.
"""
import pytest
from fastapi.testclient import TestClient

import app as app_module


@pytest.fixture()
def anonymous_client(db_session):
    """TestClient with no session cookie and no auth — exercises the
    unauthenticated /api/bootstrap path (returns user: null, empty
    user-scoped data, but still issues a CSRF token to start a session)."""
    def _override_get_db():
        yield db_session

    app_module.app.dependency_overrides[app_module.get_db] = _override_get_db
    from services.security import rate_limiter
    rate_limiter._buckets.clear()
    test_client = TestClient(app_module.app)
    yield test_client
    app_module.app.dependency_overrides.clear()


def test_bootstrap_unauthenticated_returns_null_user_and_empty_data(anonymous_client):
    r = anonymous_client.get("/api/bootstrap")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["csrf_token"], str) and len(body["csrf_token"]) >= 16
    assert body["user"] is None
    assert body["status"] is None
    assert body["recent_transcripts"] == []
    assert body["jobs"] == {"jobs": [], "active": 0}


def test_bootstrap_csrf_token_works_for_subsequent_mutation(anonymous_client):
    """The token /api/bootstrap issues must be accepted by a real mutation
    on the same session — proves the session cookie was created and the
    token was stored in it (issue #51 paranoia: rotation/session-fixation).

    Uses /api/register as the mutation (not /api/login) because the
    anonymous_client fixture has no pre-registered user to log in as;
    register is the first mutation any fresh session can perform."""
    r = anonymous_client.get("/api/bootstrap")
    token = r.json()["csrf_token"]
    anonymous_client.headers["X-CSRF-Token"] = token
    reg = anonymous_client.post(
        "/api/register",
        json={"username": "bootstrap_user", "password": "bootstrappass123"},
    )
    assert reg.status_code == 200, reg.text
    assert reg.json()["username"] == "bootstrap_user"


def test_bootstrap_authenticated_returns_full_payload(client):
    r = client.get("/api/bootstrap")
    assert r.status_code == 200
    body = r.json()
    assert isinstance(body["csrf_token"], str) and len(body["csrf_token"]) >= 16
    # The conftest's `client` fixture registers testuser as the FIRST user,
    # which services/auth.py auto-promotes to admin (see README §Accounts).
    assert body["user"] == {"username": "testuser", "is_admin": True}
    # Shape parity with /api/status — exact same keys, same types
    assert set(body["status"].keys()) == {
        "total_transcripts", "completed", "processing", "failed",
        "total_minutes", "voice_profiles", "diarization_available",
        "voice_id_backend", "backend_name",
    }
    assert body["recent_transcripts"] == []  # fresh DB
    assert body["jobs"] == {"jobs": [], "active": 0}


def test_bootstrap_status_matches_api_status_exactly(client):
    """Mirror guarantee: bootstrap.status === /api/status response, field
    for field, value for value. Drift here would let first paint show
    different data than the polling refresh that follows it (issue #143)."""
    boot = client.get("/api/bootstrap").json()["status"]
    status = client.get("/api/status").json()
    assert boot == status


def test_bootstrap_recent_transcripts_matches_api_transcripts_exactly(client):
    boot_recents = client.get("/api/bootstrap").json()["recent_transcripts"]
    recents = client.get("/api/transcripts?limit=5").json()
    assert boot_recents == recents


def test_bootstrap_jobs_matches_api_jobs_exactly(client):
    boot_jobs = client.get("/api/bootstrap").json()["jobs"]
    jobs = client.get("/api/jobs?limit=20").json()
    assert boot_jobs == jobs


def test_bootstrap_with_data_returns_expected_lists(client, db_session):
    """With one transcript and one LLM job in the DB, the bootstrap payload
    surfaces both in the right fields, and they still match the individual
    endpoints' outputs byte-for-byte."""
    from database import Transcript, LlmJob, utcnow_naive
    t = Transcript(
        user_id=1,
        title="Standup",
        filename="standup.wav",
        duration_seconds=120.0,
        status="completed",
    )
    db_session.add(t)
    db_session.commit()
    db_session.refresh(t)
    j = LlmJob(
        user_id=1,
        transcript_id=t.id,
        kind="summary",
        provider="groq",
        model="llama-3.3-70b-versatile",
        status="pending",
    )
    db_session.add(j)
    db_session.commit()

    boot = client.get("/api/bootstrap").json()
    assert len(boot["recent_transcripts"]) == 1
    assert boot["recent_transcripts"][0]["title"] == "Standup"
    assert boot["jobs"]["active"] == 1
    assert len(boot["jobs"]["jobs"]) == 1
    assert boot["jobs"]["jobs"][0]["kind"] == "summary"

    # Mirror check after the data lands
    assert boot["status"] == client.get("/api/status").json()
    assert boot["recent_transcripts"] == client.get("/api/transcripts?limit=5").json()
    assert boot["jobs"] == client.get("/api/jobs?limit=20").json()
