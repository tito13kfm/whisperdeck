"""LLM job queue: enqueue/dedupe, worker execution with progress + cancel,
rerun, and the unified /api/jobs routes."""
import asyncio
import io
import json
from unittest.mock import AsyncMock, patch

from database import LlmJob, Transcript, User, ProviderConfig
from services.llm_jobs import (
    enqueue_llm_job, run_llm_job, cancel_llm_job, rerun_llm_job, llm_worker_tick,
)


def _make_user_and_transcript(db_session, segments=None):
    user = User(username="queueop", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(
        user_id=user.id, title="q", filename="q.mp3", status="completed",
        full_text="raw", segments=segments or [],
    )
    db_session.add(t)
    db_session.add(ProviderConfig(user_id=user.id, name="groq", api_key="fake-key"))
    db_session.commit()
    return user, t


class _FakeResponse:
    def __init__(self, content):
        self.status_code = 200
        self._payload = {"choices": [{"message": {"content": content}}]}
        self.text = json.dumps(self._payload)

    def json(self):
        return self._payload


def _session_factory(db_session):
    return lambda: db_session


class _NoCloseSession:
    """run_llm_job closes its session; tests share one — swallow the close."""
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._db, name)


def test_enqueue_dedupes_active_jobs(db_session):
    user, t = _make_user_and_transcript(db_session)
    a = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    b = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m2")
    assert a.id == b.id
    # a different kind is its own lane
    c = enqueue_llm_job(db_session, user.id, t.id, "summary", "groq", "m1")
    assert c.id != a.id


def test_run_llm_job_correction_completes_with_progress(db_session):
    segs = [{"start": i, "end": i + 1, "speaker": "S", "text": "word " * 60} for i in range(40)]
    user, t = _make_user_and_transcript(db_session, segments=segs)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "llama-3.3-70b-versatile")
    job.status = "running"
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse("S: fixed"))
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "completed"
    assert job.progress_total > 1
    assert job.progress_done == job.progress_total
    db_session.refresh(t)
    assert t.corrected_text


def test_cancel_between_batches_stops_cleanly(db_session):
    segs = [{"start": i, "end": i + 1, "speaker": "S", "text": "word " * 60} for i in range(40)]
    user, t = _make_user_and_transcript(db_session, segments=segs)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m")
    job.status = "running"
    db_session.commit()

    calls = 0

    async def flip_then_respond(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            # cancel lands while the first batch is in flight
            job.status = "cancelled"
            db_session.commit()
        return _FakeResponse("S: fixed")

    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", AsyncMock(side_effect=flip_then_respond)):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "cancelled"
    assert calls == 1  # second batch never started
    db_session.refresh(t)
    assert not t.corrected_text  # partial output never lands


def test_run_llm_job_fails_without_key(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "openrouter", "m")
    job.status = "running"
    db_session.commit()
    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(run_llm_job(factory, job.id, transcription_service=None))
    db_session.refresh(job)
    assert job.status == "failed"
    assert "no openrouter API key" in job.error


def test_rerun_creates_fresh_pending_job(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m")
    job.status = "failed"
    db_session.commit()
    fresh = rerun_llm_job(db_session, user.id, job.id)
    assert fresh.id != job.id
    assert fresh.status == "pending"
    assert (fresh.provider, fresh.model, fresh.kind) == (job.provider, job.model, job.kind)


def test_cancel_requires_active_job(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m")
    cancel_llm_job(db_session, user.id, job.id)
    db_session.refresh(job)
    assert job.status == "cancelled"
    try:
        cancel_llm_job(db_session, user.id, job.id)
        assert False, "second cancel should raise"
    except ValueError:
        pass


# ── routes ────────────────────────────────────────────────────────────────

def _upload(client):
    async def _stub_transcribe(db, user_id, **kwargs):
        t = Transcript(user_id=user_id, title="t", filename="f.mp3", status="completed", full_text="hello")
        db.add(t)
        db.commit()
        return t
    with patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)):
        return client.post(
            "/api/transcribe",
            files={"file": ("m.mp3", io.BytesIO(b"x"), "audio/mpeg")},
            data={"provider": "groq"},
        )


def test_jobs_listing_and_cancel_rerun_routes(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    transcript_id = _upload(client).json()["id"]

    jobs = client.get("/api/jobs").json()
    assert jobs["active"] >= 1
    llm_entries = [j for j in jobs["jobs"] if j["kind"] == "correction"]
    assert llm_entries and llm_entries[0]["transcript_id"] == transcript_id
    assert llm_entries[0]["title"]

    job_id = llm_entries[0]["id"]
    cancelled = client.post(f"/api/jobs/{job_id}/cancel").json()
    assert cancelled["job"]["status"] == "cancelled"

    rerun = client.post(f"/api/jobs/{job_id}/rerun").json()
    assert rerun["job"]["status"] == "pending"
    assert rerun["job"]["id"] != job_id


def test_summarize_route_enqueues_job(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    client.put("/api/settings", json={"auto_correct": False})
    transcript_id = _upload(client).json()["id"]

    r = client.post(f"/api/transcripts/{transcript_id}/summarize", data={"provider": "groq", "model": "m"})
    assert r.status_code == 200
    job = r.json()["job"]
    assert job["kind"] == "summary"
    assert job["status"] == "pending"

    detail = client.get(f"/api/transcripts/{transcript_id}").json()
    assert detail["summary_job"]["id"] == job["id"]


def test_worker_tick_claims_pending_jobs(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "llama-3.3-70b-versatile")

    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=_FakeResponse("fixed"))):
        asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "completed"
