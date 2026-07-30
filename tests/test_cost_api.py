"""Tests for cost analytics API endpoints and serializer cost fields.

Mutation-check standard: each test must fail if the function under test
returned a constant — a paid-provider test that asserts cost > 0.0
would break if the function always returned 0.0.
"""
from datetime import datetime, timedelta, timezone

from database import Transcript, LlmJob, User


# ── helpers ─────────────────────────────────────────────────────────────────

def _make_user(db_session):
    """Return the test user already created by the client fixture (testuser)."""
    user = db_session.query(User).filter(User.username == "testuser").first()
    if not user:
        user = User(username="testuser", password_hash="x", password_salt="y")
        db_session.add(user)
        db_session.commit()
    return user


def _make_transcript(db_session, user, provider="groq", model="whisper-large-v3-flash",
                     duration=60.0, status="completed"):
    t = Transcript(
        user_id=user.id, title="ta", filename="ta.mp3",
        provider=provider, model=model,
        duration_seconds=duration, status=status,
    )
    db_session.add(t)
    db_session.commit()
    return t


# ── POST /api/costs/estimate ────────────────────────────────────────────────

def test_estimate_valid(client):
    """Valid request returns cost estimate with correct rate."""
    resp = client.post("/api/costs/estimate", json={
        "provider": "groq",
        "model": "whisper-large-v3-flash",
        "duration_seconds": 300.0,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["cost"] == 0.02
    assert data["rate_per_minute"] == 0.004
    assert "Groq" in data["rate_source"]


def test_estimate_missing_provider(client):
    """Missing provider returns 400, not 500."""
    resp = client.post("/api/costs/estimate", json={
        "model": "whisper-large-v3-flash",
        "duration_seconds": 300.0,
    })
    assert resp.status_code == 400
    assert "provider" in resp.json()["detail"].lower()


def test_estimate_missing_model(client):
    """Missing model returns 400, not 500."""
    resp = client.post("/api/costs/estimate", json={
        "provider": "groq",
        "duration_seconds": 300.0,
    })
    assert resp.status_code == 400
    assert "model" in resp.json()["detail"].lower()


def test_estimate_missing_duration(client):
    """Missing duration returns 400, not 500."""
    resp = client.post("/api/costs/estimate", json={
        "provider": "groq",
        "model": "whisper-large-v3-flash",
    })
    assert resp.status_code == 400
    assert "duration" in resp.json()["detail"].lower()


def test_estimate_negative_duration(client):
    """Negative duration returns 400, not 500."""
    resp = client.post("/api/costs/estimate", json={
        "provider": "groq",
        "model": "whisper-large-v3-flash",
        "duration_seconds": -10,
    })
    assert resp.status_code == 400
    assert "non-negative" in resp.json()["detail"].lower()


def test_estimate_unknown_provider_noraises(client):
    """Unknown provider returns estimate with cost 0.0, not 500."""
    resp = client.post("/api/costs/estimate", json={
        "provider": "madeup",
        "model": "fakemodel",
        "duration_seconds": 300.0,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["cost"] == 0.0
    assert "unknown" in data["rate_source"]


def test_estimate_local_provider_free(client):
    """Local (builtin) provider returns cost 0.0."""
    resp = client.post("/api/costs/estimate", json={
        "provider": "builtin",
        "model": "tiny",
        "duration_seconds": 300.0,
    })
    assert resp.status_code == 200
    data = resp.json()
    assert data["cost"] == 0.0
    assert "free" in data["rate_source"].lower()


# ── GET /api/transcripts/{id}/cost ────────────────────────────────────────

def test_transcript_cost_endpoint_includes_stt_llm(db_session, client):
    """Endpoint returns full breakdown with stt/correction/summary/total."""
    user = _make_user(db_session)
    t = _make_transcript(db_session, user, provider="groq",
                          model="whisper-large-v3-flash",
                          duration=120.0, status="completed")

    resp = client.get(f"/api/transcripts/{t.id}/cost")
    assert resp.status_code == 200
    data = resp.json()
    assert data["stt"]["cost"] == 0.008  # 120/60*0.004
    assert data["stt"]["rate_per_minute"] == 0.004
    assert data["total"] == 0.008
    assert "correction" in data
    assert "summary" in data


def test_transcript_cost_endpoint_local_free(db_session, client):
    """Local transcript returns cost 0.0 via endpoint."""
    user = _make_user(db_session)
    t = _make_transcript(db_session, user, provider="builtin",
                          model="tiny", duration=60.0, status="completed")

    resp = client.get(f"/api/transcripts/{t.id}/cost")
    assert resp.status_code == 200
    data = resp.json()
    assert data["stt"]["cost"] == 0.0
    assert data["stt"]["rate_per_minute"] == 0.0
    assert data["total"] == 0.0


def test_transcript_cost_endpoint_not_found(client):
    """Non-existent transcript returns 404."""
    resp = client.get("/api/transcripts/99999/cost")
    assert resp.status_code == 404


# ── GET /api/costs ─────────────────────────────────────────────────────────

def test_costs_endpoint_aggregates(db_session, client):
    """Costs endpoint returns per-provider aggregates + totals."""
    user = _make_user(db_session)
    _make_transcript(db_session, user, provider="groq",
                      model="whisper-large-v3-flash",
                      duration=120.0, status="completed")
    _make_transcript(db_session, user, provider="groq",
                      model="whisper-large-v3-turbo",
                      duration=180.0, status="completed")
    _make_transcript(db_session, user, provider="builtin",
                      model="tiny", duration=60.0, status="completed")

    resp = client.get("/api/costs")
    assert resp.status_code == 200
    data = resp.json()
    assert "providers" in data
    assert "monthly_total" in data
    assert "lifetime_total" in data
    assert data["monthly_total"] > 0  # 300s of groq at 0.004/min = 0.02
    assert data["lifetime_total"] > 0
    assert data["monthly_total"] == 0.02
    # builtin should also appear
    # groq should appear in provider breakdown
    assert any(p == "groq" for p in data["providers"])


def test_costs_endpoint_no_transcripts(client):
    """Costs endpoint works with no transcripts (empty result)."""
    resp = client.get("/api/costs")
    assert resp.status_code == 200
    data = resp.json()
    assert data["providers"] == {}
    assert data["monthly_total"] == 0.0
    assert data["lifetime_total"] == 0.0


# ── serializer cost field: detail ─────────────────────────────────────────

def test_serialize_transcript_includes_cost(db_session, client):
    """_serialize_transcript (detail view) includes a cost breakdown."""
    user = _make_user(db_session)
    t = _make_transcript(db_session, user, provider="groq",
                          model="whisper-large-v3-flash",
                          duration=120.0, status="completed")

    resp = client.get(f"/api/transcripts/{t.id}")
    assert resp.status_code == 200
    data = resp.json()
    assert "cost" in data
    assert isinstance(data["cost"], dict)
    assert data["cost"]["stt"]["cost"] == 0.008
    assert data["cost"]["stt"]["rate_per_minute"] == 0.004


# ── serializer cost field: summary (list view) ────────────────────────────

def test_serialize_transcript_summary_includes_cost(db_session, client):
    """_serialize_transcript_summary (list view) includes a cost value."""
    user = _make_user(db_session)
    _make_transcript(db_session, user, provider="groq",
                      model="whisper-large-v3-flash",
                      duration=120.0, status="completed")

    resp = client.get("/api/transcripts")
    assert resp.status_code == 200
    transcripts = resp.json()
    assert isinstance(transcripts, list)
    assert len(transcripts) >= 1
    t = transcripts[0]
    assert "cost" in t
    assert isinstance(t["cost"], (int, float))
    # 120/60 * 0.004 = 0.008
    assert t["cost"] == 0.008


def test_serialize_transcript_summary_cost_local_free(db_session, client):
    """List view cost is 0.0 for local (free) transcript."""
    user = _make_user(db_session)
    _make_transcript(db_session, user, provider="builtin",
                      model="tiny", duration=60.0, status="completed")

    resp = client.get("/api/transcripts")
    assert resp.status_code == 200
    transcripts = resp.json()
    assert isinstance(transcripts, list)
    assert len(transcripts) >= 1
    t = transcripts[0]
    assert t["cost"] == 0.0


# ── _batch_stt_costs helper ────────────────────────────────────────────────

def test_batch_stt_costs_computes_per_id(db_session):
    """_batch_stt_costs returns {id: cost} for a list of transcripts."""
    from app import _batch_stt_costs
    user = _make_user(db_session)
    t1 = _make_transcript(db_session, user, provider="groq",
                           model="whisper-large-v3-flash",
                           duration=60.0, status="completed")
    t2 = _make_transcript(db_session, user, provider="openai",
                           model="whisper-1",
                           duration=120.0, status="completed")

    costs = _batch_stt_costs([t1, t2])
    assert costs[t1.id] == 0.004   # 60/60 * 0.004
    assert costs[t2.id] == 0.012   # 120/60 * 0.006


def test_batch_stt_costs_no_duration_returns_zero(db_session):
    """Transcripts with no duration get cost 0.0."""
    from app import _batch_stt_costs
    user = _make_user(db_session)
    t = _make_transcript(db_session, user, provider="groq",
                          model="whisper-large-v3-flash",
                          duration=None, status="completed")
    costs = _batch_stt_costs([t])
    assert costs[t.id] == 0.0


def test_batch_stt_costs_no_provider_returns_zero(db_session):
    """Transcripts with empty provider get cost 0.0."""
    from app import _batch_stt_costs
    user = _make_user(db_session)
    t = _make_transcript(db_session, user, provider="",
                          model="", duration=60.0, status="completed")
    costs = _batch_stt_costs([t])
    assert costs[t.id] == 0.0


# ── openrouter LlmJob cost path (_resolve_openrouter_rate) ────────────────

def test_transcript_cost_endpoint_openrouter_llm_job_no_crash(db_session, client):
    """A completed OpenRouter correction job must not crash the /cost
    endpoint. The rate lookup runs from inside the request's own event loop,
    so it must be skipped there (not attempted via asyncio.run, which would
    raise RuntimeError for a loop already running)."""
    user = _make_user(db_session)
    t = _make_transcript(db_session, user, provider="groq",
                          model="whisper-large-v3-flash",
                          duration=60.0, status="completed")
    job = LlmJob(user_id=user.id, transcript_id=t.id, kind="correction",
                 status="completed", provider="openrouter",
                 model="deepseek/deepseek-v4-flash")
    db_session.add(job)
    db_session.commit()

    resp = client.get(f"/api/transcripts/{t.id}/cost")
    assert resp.status_code == 200
    data = resp.json()
    assert data["correction"]["cost"] == 0.0
    assert data["correction"]["rate_source"] == \
        "OpenRouter (rate lookup skipped — called from an async context)"


# ── rate_limit_gauge in /api/costs & /api/jobs ──────────────────────────

def test_costs_endpoint_includes_rate_limit_gauge(db_session, client):
    """GET /api/costs includes a structured rate_limit_gauge object."""
    user = _make_user(db_session)
    _make_transcript(db_session, user, provider="groq",
                      model="whisper-large-v3-flash",
                      duration=120.0, status="completed")

    resp = client.get("/api/costs")
    assert resp.status_code == 200
    data = resp.json()
    assert "rate_limit_gauge" in data
    gauge = data["rate_limit_gauge"]
    assert gauge["provider"] == "groq"
    assert gauge["used_seconds"] == 120.0
    assert gauge["limit_seconds"] == 28800
    assert gauge["used_cost"] == 0.008
    assert gauge["limit_cost"] == 1.92


def test_jobs_endpoint_includes_rate_limit_gauge(db_session, client):
    """GET /api/jobs includes a structured rate_limit_gauge object."""
    user = _make_user(db_session)
    _make_transcript(db_session, user, provider="groq",
                      model="whisper-large-v3-flash",
                      duration=60.0, status="completed")

    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    data = resp.json()
    assert "rate_limit_gauge" in data
    gauge = data["rate_limit_gauge"]
    assert gauge["provider"] == "groq"
    assert gauge["used_seconds"] == 60.0
    assert gauge["limit_seconds"] > 0
    assert gauge["used_cost"] == 0.004
