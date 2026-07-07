"""LLM job queue: enqueue/dedupe, worker execution with progress + cancel,
rerun, and the unified /api/jobs routes."""
import asyncio
import io
import json
from unittest.mock import AsyncMock, patch

from database import LlmJob, Transcript, User, ProviderConfig
from services.llm_jobs import (
    enqueue_llm_job, run_llm_job, cancel_llm_job, rerun_llm_job, llm_worker_tick,
    reset_stuck_llm_jobs, dismiss_llm_job, clear_finished_llm_jobs,
)


def _make_user_and_transcript(db_session, segments=None, username="queueop"):
    user = User(username=username, password_hash="x", password_salt="y")
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


def test_reset_stuck_llm_jobs_fails_running_and_leaves_others_alone(db_session):
    user, t = _make_user_and_transcript(db_session)
    running = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    running.status = "running"
    pending = enqueue_llm_job(db_session, user.id, t.id, "summary", "groq", "m1")
    completed = enqueue_llm_job(db_session, user.id, t.id, "rediarize", "groq", "m1")
    completed.status = "completed"
    db_session.commit()

    count = reset_stuck_llm_jobs(db_session)

    assert count == 1
    db_session.refresh(running)
    db_session.refresh(pending)
    db_session.refresh(completed)
    assert running.status == "failed"
    assert running.error == "Interrupted by server restart"
    assert pending.status == "pending"
    assert completed.status == "completed"


def test_dismiss_llm_job_requires_terminal_status(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "running"
    db_session.commit()

    try:
        dismiss_llm_job(db_session, user.id, job.id)
        assert False, "expected ValueError for a running job"
    except ValueError:
        pass

    job.status = "completed"
    db_session.commit()
    dismissed = dismiss_llm_job(db_session, user.id, job.id)
    assert dismissed.dismissed is True


def test_dismiss_llm_job_scoped_to_owner(db_session):
    user, t = _make_user_and_transcript(db_session)
    other, _ = _make_user_and_transcript(db_session, username="queueop2")
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "completed"
    db_session.commit()

    try:
        dismiss_llm_job(db_session, other.id, job.id)
        assert False, "expected LookupError for another user's job"
    except LookupError:
        pass

    try:
        dismiss_llm_job(db_session, user.id, 999999)
        assert False, "expected LookupError for a nonexistent job"
    except LookupError:
        pass


def test_clear_finished_llm_jobs_only_touches_terminal_undismissed_for_owner(db_session):
    user, t = _make_user_and_transcript(db_session)
    other, other_t = _make_user_and_transcript(db_session, username="queueop3")

    done = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    done.status = "completed"
    running = enqueue_llm_job(db_session, user.id, t.id, "summary", "groq", "m1")
    running.status = "running"
    already_dismissed = enqueue_llm_job(db_session, user.id, t.id, "rediarize", "groq", "m1")
    already_dismissed.status = "failed"
    already_dismissed.dismissed = True
    other_done = enqueue_llm_job(db_session, other.id, other_t.id, "correction", "groq", "m1")
    other_done.status = "completed"
    db_session.commit()

    count = clear_finished_llm_jobs(db_session, user.id)

    assert count == 1
    db_session.refresh(done)
    db_session.refresh(running)
    db_session.refresh(other_done)
    assert done.dismissed is True
    assert running.dismissed is False
    assert other_done.dismissed is False  # another user's job untouched


def test_run_llm_job_correction_uses_local_llm_url_independent_of_stt(db_session):
    """local (transcription) and local_llm (correction/summary) are separate
    ProviderConfig rows — the job runner must resolve the one matching the
    job's provider, not conflate the two even though both are keyless."""
    user, t = _make_user_and_transcript(db_session)
    db_session.add(ProviderConfig(user_id=user.id, name="local", api_url="http://stt-box:8080/v1"))
    db_session.add(ProviderConfig(user_id=user.id, name="local_llm", api_url="http://llm-box:11434/v1"))
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "local_llm", "llama3")
    job.status = "running"
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse("fixed"))
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "completed"
    assert fake_post.await_args.args[0].startswith("http://llm-box:11434/v1")


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


def test_run_llm_job_correction_saves_result_snapshot(db_session):
    segs = [{"start": 0, "end": 1, "speaker": "S", "text": "hello"}]
    user, t = _make_user_and_transcript(db_session, segments=segs)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m")
    job.status = "running"
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse("S: fixed hello"))
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.result_json == {"corrected_text": t.corrected_text}


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


def test_summary_endpoint_exposes_provider(db_session):
    from database import Summary
    from app import _serialize_summary
    user, t = _make_user_and_transcript(db_session)
    db_session.add(Summary(transcript_id=t.id, short_summary="s", model="m1", provider="groq"))
    db_session.commit()
    db_session.refresh(t)
    result = _serialize_summary(t.summary)
    assert result["provider"] == "groq"


def test_worker_tick_claims_pending_jobs(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "llama-3.3-70b-versatile")

    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=_FakeResponse("fixed"))):
        asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "completed"


def test_runs_endpoint_lists_correction_history_newest_first(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    client.put("/api/settings", json={"auto_correct": False})
    transcript_id = _upload(client).json()["id"]

    first = client.post(f"/api/transcripts/{transcript_id}/correct", data={"provider": "groq", "model": "m1"}).json()["job"]
    client.post(f"/api/jobs/{first['id']}/cancel")
    second = client.post(f"/api/jobs/{first['id']}/rerun").json()["job"]

    runs = client.get(f"/api/transcripts/{transcript_id}/runs/correction").json()["runs"]
    assert [r["id"] for r in runs] == [second["id"], first["id"]]
    assert runs[0]["provider"] == "groq" and runs[0]["model"] == "m1"


def test_runs_endpoint_rejects_unknown_kind(client):
    transcript_id = _upload(client).json()["id"]
    r = client.get(f"/api/transcripts/{transcript_id}/runs/bogus")
    assert r.status_code == 400


def test_runs_endpoint_includes_dismissed_jobs(client):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    client.put("/api/settings", json={"auto_correct": False})
    transcript_id = _upload(client).json()["id"]

    job = client.post(f"/api/transcripts/{transcript_id}/correct", data={"provider": "groq", "model": "m1"}).json()["job"]
    client.post(f"/api/jobs/{job['id']}/cancel")
    client.post(f"/api/jobs/{job['id']}/dismiss")

    runs = client.get(f"/api/transcripts/{transcript_id}/runs/correction").json()["runs"]
    assert [r["id"] for r in runs] == [job["id"]]


def test_runs_endpoint_404s_for_another_users_transcript(client):
    transcript_id = _upload(client).json()["id"]
    client.post("/api/logout")
    client.post("/api/register", json={"username": "other-runs-user", "password": "testpass123"})

    r = client.get(f"/api/transcripts/{transcript_id}/runs/correction")
    assert r.status_code == 404
