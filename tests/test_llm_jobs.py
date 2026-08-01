"""LLM job queue: enqueue/dedupe, worker execution with progress + cancel,
rerun, and the unified /api/jobs routes."""
import asyncio
import datetime
import io
import json
from unittest.mock import AsyncMock, patch

from database import LlmJob, Transcript, User, ProviderConfig, utcnow_naive
from services.llm_jobs import (
    enqueue_llm_job, run_llm_job, cancel_llm_job, rerun_llm_job, llm_worker_tick,
    reset_stuck_llm_jobs, dismiss_llm_job, clear_finished_llm_jobs, serialize_llm_job,
)
from services.queue import MAX_ATTEMPTS


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


def test_run_llm_job_summary_saves_result_snapshot(db_session, tmp_path):
    from services.transcription import TranscriptionService
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "summary", "groq", "m1")
    job.status = "running"
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse(
        '{"short_summary": "s", "key_points": ["a"], "action_items": [], "decisions": []}'
    ))
    factory = lambda: _NoCloseSession(db_session)
    svc = TranscriptionService(str(tmp_path))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=svc))

    db_session.refresh(job)
    assert job.result_json == {"short_summary": "s", "key_points": ["a"], "action_items": [], "decisions": []}


def test_run_llm_job_rediarize_saves_result_snapshot(db_session, tmp_path):
    segs = [{"start": 0, "end": 1, "speaker": "Speaker A", "text": "hi"}]
    user, t = _make_user_and_transcript(db_session, segments=segs)
    audio_path = tmp_path / "a.mp3"
    audio_path.write_bytes(b"x")
    t.audio_path = str(audio_path)
    db_session.commit()

    job = enqueue_llm_job(db_session, user.id, t.id, "rediarize", "", "")
    job.status = "running"
    db_session.commit()

    new_segments = [{"start": 0, "end": 1, "speaker": "Speaker B", "text": "hi"}]

    class _FakeDiarizationService:
        async def diarize_and_merge(self, *args, **kwargs):
            return new_segments, 1, "pyannote"

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(run_llm_job(factory, job.id, transcription_service=None, diarization_service=_FakeDiarizationService()))

    db_session.refresh(job)
    assert job.result_json == {"segments": new_segments}


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


def test_cancel_zeros_progress_in_db(db_session):
    """cancel_llm_job must zero progress_done and progress_total so
    serialize_llm_job doesn't report stale partial progress on cancelled jobs."""
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m")
    job.progress_done = 5
    job.progress_total = 10
    db_session.commit()

    cancel_llm_job(db_session, user.id, job.id)
    db_session.refresh(job)

    assert job.status == "cancelled"
    assert job.progress_done == 0
    assert job.progress_total == 0

    serialized = serialize_llm_job(job)
    assert serialized["progress"] == {"done": 0, "total": 0}


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
    # Logout clears the whole session, invalidating the CSRF token issued to
    # it — fetch a fresh one before the next mutation (mirrors rack.js's
    # logout() -> refreshCsrfToken()).
    client.headers["X-CSRF-Token"] = client.get("/api/csrf-token").json()["token"]
    client.post("/api/register", json={"username": "other-runs-user", "password": "testpass123"})

    r = client.get(f"/api/transcripts/{transcript_id}/runs/correction")
    assert r.status_code == 404


def test_io_cpu_pools_partition_valid_kinds():
    """Sanity check: every valid job kind must belong to exactly one pool,
    so a future kind addition can't silently fall through uncapped."""
    from services.llm_jobs import IO_KINDS, CPU_KINDS, VALID_KINDS
    assert set(IO_KINDS) | set(CPU_KINDS) == set(VALID_KINDS)
    assert set(IO_KINDS) & set(CPU_KINDS) == set()


# --- Studio pipeline classification (issue #267) ---------------------------

def test_enqueue_pipeline_classify_noops_when_not_pending(db_session):
    """Nothing sets classification_status='pending' yet (issue #268
    introduces the 'auto' kind sentinel) — the default 'override' status
    must make this enqueue a no-op, matching the other kind-gated helpers'
    belt-and-braces pattern."""
    from services.llm_jobs import enqueue_pipeline_classify
    user, t = _make_user_and_transcript(db_session)
    assert t.classification_status == "override"
    job = enqueue_pipeline_classify(db_session, t, {})
    assert job is None
    assert db_session.query(LlmJob).filter(LlmJob.transcript_id == t.id, LlmJob.kind == "classify_pipeline").count() == 0


def test_enqueue_pipeline_classify_fires_when_pending(db_session):
    from services.llm_jobs import enqueue_pipeline_classify
    user, t = _make_user_and_transcript(db_session)
    t.classification_status = "pending"
    db_session.commit()
    job = enqueue_pipeline_classify(db_session, t, {"classification_provider": "local_llm", "classification_model": "m"})
    assert job is not None
    assert job.kind == "classify_pipeline"
    assert job.status == "pending"


def test_run_llm_job_correction_completion_triggers_pipeline_classify_when_pending(db_session):
    user, t = _make_user_and_transcript(db_session)
    t.classification_status = "pending"
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "running"
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse("S: fixed"))
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    classify_job = (
        db_session.query(LlmJob)
        .filter(LlmJob.transcript_id == t.id, LlmJob.kind == "classify_pipeline")
        .first()
    )
    assert classify_job is not None
    assert classify_job.status == "pending"


def test_run_llm_job_correction_completion_skips_pipeline_classify_when_override(db_session):
    """Default state today — every transcript has an explicit kind, so
    correction completing must NOT spuriously enqueue a classification job."""
    user, t = _make_user_and_transcript(db_session)
    assert t.classification_status == "override"
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "running"
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse("S: fixed"))
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    count = db_session.query(LlmJob).filter(LlmJob.transcript_id == t.id, LlmJob.kind == "classify_pipeline").count()
    assert count == 0


def test_run_llm_job_classify_pipeline_accepts_above_threshold(db_session):
    user, t = _make_user_and_transcript(db_session)
    t.classification_status = "pending"
    t.kind = "meeting"  # placeholder pre-classification value
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "classify_pipeline", "groq", "m1")
    job.status = "running"
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse('{"kind": "dictation", "confidence": 0.95}'))
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "completed"
    assert job.result_json == {"kind": "dictation", "confidence": 0.95, "accepted": True}
    assert t.kind == "dictation"
    assert t.classification_status == "success"
    assert t.classification_confidence == 0.95
    assert t.classification_provenance["provider"] == "groq"
    assert t.classification_provenance["schema_version"] == 1
    assert "classified_at" in t.classification_provenance


def test_run_llm_job_classify_pipeline_stays_uncertain_below_threshold(db_session):
    """Below the confidence threshold (default 0.75): status becomes
    'uncertain', but Transcript.kind is NOT overwritten — an unconfident
    guess must never silently change routing (design decision 3/8)."""
    user, t = _make_user_and_transcript(db_session)
    t.classification_status = "pending"
    t.kind = "meeting"
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "classify_pipeline", "groq", "m1")
    job.status = "running"
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse('{"kind": "voice_note", "confidence": 0.4}'))
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "completed"
    assert job.result_json["accepted"] is False
    assert t.kind == "meeting"  # unchanged
    assert t.classification_status == "uncertain"
    assert t.classification_confidence == 0.4


def test_run_llm_job_classify_pipeline_fails_retryably_on_malformed_response(db_session):
    """A malformed/empty classifier response must land the job 'failed' (not
    silently default to a fallback kind) so AUTO_RETRY_KINDS' retry sweep can
    resurrect it — this is what makes 'failure leaves a safe, retryable
    state' (issue #267 acceptance) actually true. transcript.classification_status
    must also move to 'failed', not stay at 'pending' — 'pending' would be
    indistinguishable from "never attempted" (PR #273 review finding)."""
    user, t = _make_user_and_transcript(db_session)
    t.classification_status = "pending"
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "classify_pipeline", "groq", "m1")
    job.status = "running"
    job.attempts = 1
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse("not json"))
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "failed"
    assert t.classification_status == "failed"
    assert t.classification_confidence is None
    from services.llm_jobs import serialize_llm_job
    assert serialize_llm_job(job)["will_retry"] is True


def test_run_llm_job_classify_pipeline_failure_respects_concurrent_cancel(db_session):
    """If a cancel wins the race against a classifier failure, the cancel
    must stick — transcript.classification_status must NOT be overwritten
    to 'failed' out from under a cancel the user just issued."""
    user, t = _make_user_and_transcript(db_session)
    t.classification_status = "pending"
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "classify_pipeline", "groq", "m1")
    job.status = "running"
    db_session.commit()

    async def _raise_and_cancel(*args, **kwargs):
        # Simulate a cancel landing (via a separate request) while the
        # classifier call is in flight, just before it raises.
        job.status = "cancelled"
        db_session.commit()
        raise RuntimeError("provider exploded")

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.classification.classify_pipeline_kind", _raise_and_cancel):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "cancelled"
    assert t.classification_status == "pending"  # untouched by the failure path


def test_run_llm_job_correction_cancel_race_skips_pipeline_classify(db_session):
    """A cancel that wins the race between correct_transcript() returning
    'ok' and _finish() running must leave the job 'cancelled' — and, since
    the correction didn't actually complete, must NOT enqueue classification
    (PR #273 review finding)."""
    user, t = _make_user_and_transcript(db_session)
    t.classification_status = "pending"
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "running"
    db_session.commit()

    async def _correct_then_cancel(db, transcript, **kwargs):
        # Simulate a cancel landing (via a separate request) between the
        # correction pass finishing and this job's own _finish() call.
        transcript.corrected_text = "fixed"
        transcript.correction_model = "groq/m1"
        db.commit()
        job.status = "cancelled"
        db.commit()
        return "ok"

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.correction.correct_transcript", _correct_then_cancel):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    count = db_session.query(LlmJob).filter(LlmJob.transcript_id == t.id, LlmJob.kind == "classify_pipeline").count()
    assert job.status == "cancelled"
    assert count == 0


def test_run_llm_job_correction_failure_triggers_pipeline_classify_when_pending(db_session):
    """Issue #268 comment 2's gap: if correction itself fails, the usual
    trigger (correction completing 'ok') never runs — classification must
    still get a chance against whatever text is available (full_text),
    matching services/classification.py's own fallback."""
    user, t = _make_user_and_transcript(db_session)
    t.classification_status = "pending"
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "running"
    db_session.commit()

    async def _correction_fails(db, transcript, **kwargs):
        transcript.correction_error = "provider exploded"
        db.commit()
        return "failed"

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.correction.correct_transcript", _correction_fails):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"
    classify_job = (
        db_session.query(LlmJob)
        .filter(LlmJob.transcript_id == t.id, LlmJob.kind == "classify_pipeline")
        .first()
    )
    assert classify_job is not None
    assert classify_job.status == "pending"


def test_run_llm_job_correction_failure_skips_pipeline_classify_when_override(db_session):
    user, t = _make_user_and_transcript(db_session)
    assert t.classification_status == "override"
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "running"
    db_session.commit()

    async def _correction_fails(db, transcript, **kwargs):
        transcript.correction_error = "provider exploded"
        db.commit()
        return "failed"

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.correction.correct_transcript", _correction_fails):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    count = db_session.query(LlmJob).filter(LlmJob.transcript_id == t.id, LlmJob.kind == "classify_pipeline").count()
    assert count == 0


def test_run_llm_job_correction_failure_cancel_race_skips_pipeline_classify(db_session):
    """A cancel that wins the race against a correction failure must leave
    the job 'cancelled' and must NOT trigger classification — mirrors the
    existing success-path cancel-race protection."""
    user, t = _make_user_and_transcript(db_session)
    t.classification_status = "pending"
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "running"
    db_session.commit()

    async def _fail_then_cancel(db, transcript, **kwargs):
        transcript.correction_error = "provider exploded"
        db.commit()
        job.status = "cancelled"
        db.commit()
        return "failed"

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.correction.correct_transcript", _fail_then_cancel):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    count = db_session.query(LlmJob).filter(LlmJob.transcript_id == t.id, LlmJob.kind == "classify_pipeline").count()
    assert job.status == "cancelled"
    assert count == 0


def test_run_llm_job_classify_pipeline_accepted_voice_note_retroactively_enqueues_chain(db_session):
    """Design decision 11 (services/llm_jobs.py:203 row): the voice-note
    chain must be triggered retroactively once a pending transcript
    resolves to voice_note -- there's no earlier dispatch-time call site
    that already knows the kind for an 'auto' upload."""
    user, t = _make_user_and_transcript(db_session)
    t.classification_status = "pending"
    t.kind = "meeting"
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "classify_pipeline", "groq", "m1")
    job.status = "running"
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse('{"kind": "voice_note", "confidence": 0.95}'))
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(t)
    assert t.kind == "voice_note"
    voice_note_job = (
        db_session.query(LlmJob)
        .filter(LlmJob.transcript_id == t.id, LlmJob.kind == "voice_note")
        .first()
    )
    assert voice_note_job is not None


def test_run_llm_job_classify_pipeline_accepted_dictation_does_not_enqueue_voice_note_chain(db_session):
    user, t = _make_user_and_transcript(db_session)
    t.classification_status = "pending"
    t.kind = "meeting"
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "classify_pipeline", "groq", "m1")
    job.status = "running"
    db_session.commit()

    fake_post = AsyncMock(return_value=_FakeResponse('{"kind": "dictation", "confidence": 0.95}'))
    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    count = db_session.query(LlmJob).filter(LlmJob.transcript_id == t.id, LlmJob.kind == "voice_note").count()
    assert count == 0


def test_worker_tick_io_cap_limits_dispatch(db_session, tmp_path):
    """IO pool cap defaults to 2 — one already-running IO job plus one
    freshly claimed IO job fill it; a third pending IO job must wait."""
    from services.transcription import TranscriptionService

    user1, t1 = _make_user_and_transcript(db_session, username="ioq1")
    running = enqueue_llm_job(db_session, user1.id, t1.id, "correction", "groq", "m")
    running.status = "running"
    db_session.commit()

    user2, t2 = _make_user_and_transcript(db_session, username="ioq2")
    claimable = enqueue_llm_job(db_session, user2.id, t2.id, "summary", "groq", "m1")

    user3, t3 = _make_user_and_transcript(db_session, username="ioq3")
    overflow = enqueue_llm_job(db_session, user3.id, t3.id, "correction", "groq", "m1")

    factory = lambda: _NoCloseSession(db_session)
    svc = TranscriptionService(str(tmp_path))
    fake_post = AsyncMock(return_value=_FakeResponse(
        '{"short_summary": "s", "key_points": [], "action_items": [], "decisions": []}'
    ))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(llm_worker_tick(factory, transcription_service=svc))

    db_session.refresh(running)
    db_session.refresh(claimable)
    db_session.refresh(overflow)
    assert running.status == "running"      # untouched — already counted against the cap
    assert claimable.status == "completed"  # 2nd IO slot — claimed and ran
    assert overflow.status == "pending"     # 3rd IO job — cap (2) already full, must wait


def test_worker_tick_cpu_cap_limits_dispatch(db_session):
    """CPU pool cap defaults to 1 — a second pending CPU-kind job must wait
    even though the IO pool is completely empty."""
    user1, t1 = _make_user_and_transcript(db_session, username="cpuq1")
    claimable = enqueue_llm_job(db_session, user1.id, t1.id, "rediarize", "", "")

    user2, t2 = _make_user_and_transcript(db_session, username="cpuq2")
    overflow = enqueue_llm_job(db_session, user2.id, t2.id, "rediarize", "", "")

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(llm_worker_tick(factory, transcription_service=None, diarization_service=None))

    db_session.refresh(claimable)
    db_session.refresh(overflow)
    # claimed (dispatched) even though diarization_service is unavailable —
    # it fails fast rather than being left pending, which is what proves it
    # was picked up by the claim query.
    assert claimable.status == "failed"
    assert claimable.error == "Diarization service unavailable"
    assert overflow.status == "pending"  # CPU cap (1) already full, must wait


def test_worker_tick_full_io_pool_does_not_block_cpu_dispatch(db_session):
    """The actual point of the split: two running IO-kind jobs — deliberately
    equal to the OLD shared global cap of 2 — must not stall a pending
    CPU-kind job. Under the pre-split shared cap, running=2 >= cap=2 would
    have produced slots=0 for every kind, including this rediarize job."""
    user1, t1 = _make_user_and_transcript(db_session, username="mixq1")
    io_a = enqueue_llm_job(db_session, user1.id, t1.id, "correction", "groq", "m")
    io_a.status = "running"

    user2, t2 = _make_user_and_transcript(db_session, username="mixq2")
    io_b = enqueue_llm_job(db_session, user2.id, t2.id, "correction", "groq", "m")
    io_b.status = "running"
    db_session.commit()

    user3, t3 = _make_user_and_transcript(db_session, username="mixq3")
    cpu_job = enqueue_llm_job(db_session, user3.id, t3.id, "rediarize", "", "")

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(llm_worker_tick(factory, transcription_service=None, diarization_service=None))

    db_session.refresh(cpu_job)
    assert cpu_job.status == "failed"  # dispatched then failed fast — not left pending behind the full IO pool


def test_worker_tick_full_cpu_pool_does_not_block_io_dispatch(db_session):
    """Reverse direction: two running CPU-kind jobs — deliberately equal to
    the OLD shared global cap of 2, not just the new CPU cap of 1 — must not
    stall a pending IO-kind job."""
    user1, t1 = _make_user_and_transcript(db_session, username="mixq4")
    cpu_a = enqueue_llm_job(db_session, user1.id, t1.id, "rediarize", "", "")
    cpu_a.status = "running"

    user2, t2 = _make_user_and_transcript(db_session, username="mixq5")
    cpu_b = enqueue_llm_job(db_session, user2.id, t2.id, "voice_match", "", "")
    cpu_b.status = "running"
    db_session.commit()

    user3, t3 = _make_user_and_transcript(db_session, username="mixq6")
    io_job = enqueue_llm_job(db_session, user3.id, t3.id, "correction", "groq", "m")

    factory = lambda: _NoCloseSession(db_session)
    fake_post = AsyncMock(return_value=_FakeResponse("S: fixed"))
    with patch("httpx.AsyncClient.post", fake_post):
        asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(io_job)
    assert io_job.status == "completed"  # dispatched despite the full CPU pool


def test_llm_job_attempts_defaults_to_zero(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    assert job.attempts == 0


def test_worker_tick_increments_attempts_on_claim(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    assert job.attempts == 0

    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=_FakeResponse("S: fixed"))):
        asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(job)
    assert job.attempts == 1
    assert job.status == "completed"


def test_worker_tick_resurrects_failed_job_past_backoff_window(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "failed"
    job.attempts = 1
    job.error = "transient network blip"
    job.updated_at = utcnow_naive() - datetime.timedelta(seconds=100)  # backoff for attempts=1 is 10s
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=_FakeResponse("S: fixed"))):
        asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "completed"
    assert job.attempts == 2
    assert job.error is None


def test_worker_tick_leaves_failed_job_within_backoff_window(db_session):
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "failed"
    job.attempts = 1
    job.error = "transient network blip"
    job.updated_at = utcnow_naive()  # just failed — backoff for attempts=1 is 10s, not elapsed
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 1


def test_worker_tick_never_resurrects_job_at_max_attempts(db_session):
    """status=='failed' alone can't distinguish 'the guard skipped this job'
    from 'the guard failed to skip it but the (unmocked) rerun failed again
    anyway' — both leave status=='failed'. Pin attempts unchanged too: a
    real resurrection would reclaim the row and increment attempts."""
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "failed"
    job.attempts = MAX_ATTEMPTS
    job.updated_at = utcnow_naive() - datetime.timedelta(seconds=1000)
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.attempts == MAX_ATTEMPTS  # never reclaimed by the sweep


def test_worker_tick_never_resurrects_dismissed_job(db_session):
    """See test_worker_tick_never_resurrects_job_at_max_attempts docstring —
    same discrimination problem, same fix: pin attempts unchanged."""
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "failed"
    job.attempts = 1
    job.dismissed = True
    job.updated_at = utcnow_naive() - datetime.timedelta(seconds=1000)
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 1  # never reclaimed by the sweep


def test_worker_tick_never_resurrects_a_job_that_never_ran(db_session):
    """attempts stays 0 for jobs enqueue_llm_job pre-fails immediately (e.g.
    'no API key saved') — retrying a precondition failure would just fail
    identically, so these are excluded from the auto-retry sweep. Pin
    attempts unchanged (see test_worker_tick_never_resurrects_job_at_max_attempts
    docstring for why status alone doesn't discriminate here)."""
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(
        db_session, user.id, t.id, "correction", "openrouter", "m1",
        error="auto-correct skipped: no openrouter API key saved (see service panel)",
    )
    assert job.status == "failed" and job.attempts == 0
    job.updated_at = utcnow_naive() - datetime.timedelta(seconds=1000)
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 0  # never reclaimed by the sweep


def test_worker_tick_never_resurrects_non_auto_retry_kinds(db_session):
    """See test_worker_tick_never_resurrects_job_at_max_attempts docstring —
    same discrimination problem, same fix: pin attempts unchanged."""
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "rediarize", "", "")
    job.status = "failed"
    job.attempts = 1
    job.updated_at = utcnow_naive() - datetime.timedelta(seconds=1000)
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(llm_worker_tick(factory, transcription_service=None, diarization_service=None))

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 1  # never reclaimed by the sweep


def test_serialize_will_retry_reflects_auto_retry_eligibility(db_session):
    """The frontend's background-failure toast (#58) trusts will_retry as the
    sole signal for 'this failure is terminal' — it must be False exactly
    when the auto-retry sweep (llm_worker_tick) would leave the job alone."""
    user, t = _make_user_and_transcript(db_session)

    eligible = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    eligible.status = "failed"
    eligible.attempts = MAX_ATTEMPTS - 1
    db_session.commit()
    assert serialize_llm_job(eligible)["will_retry"] is True

    exhausted = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    exhausted.status = "failed"
    exhausted.attempts = MAX_ATTEMPTS
    db_session.commit()
    assert serialize_llm_job(exhausted)["will_retry"] is False

    non_retry_kind = enqueue_llm_job(db_session, user.id, t.id, "rediarize", "", "")
    non_retry_kind.status = "failed"
    non_retry_kind.attempts = 1
    db_session.commit()
    assert serialize_llm_job(non_retry_kind)["will_retry"] is False

    still_running = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    assert still_running.status in ("pending", "running")
    assert serialize_llm_job(still_running)["will_retry"] is False

    precondition_failed = enqueue_llm_job(
        db_session, user.id, t.id, "summary", "openrouter", "m1",
        error="no openrouter API key saved (see service panel)",
    )
    assert precondition_failed.status == "failed" and precondition_failed.attempts == 0
    assert serialize_llm_job(precondition_failed)["will_retry"] is False


def test_worker_tick_skips_resurrection_when_a_fresh_job_already_active(db_session):
    """A manual rerun creates a NEW row rather than reusing the failed one
    (see rerun_llm_job) — if the auto-retry sweep resurrected the old failed
    row too, both would dispatch and write the same transcript concurrently."""
    user, t = _make_user_and_transcript(db_session)
    stale = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    stale.status = "failed"
    stale.attempts = 1
    stale.updated_at = utcnow_naive() - datetime.timedelta(seconds=1000)
    db_session.commit()

    fresh = rerun_llm_job(db_session, user.id, stale.id)
    assert fresh.status == "pending"

    factory = lambda: _NoCloseSession(db_session)
    with patch("httpx.AsyncClient.post", AsyncMock(return_value=_FakeResponse("S: fixed"))):
        asyncio.run(llm_worker_tick(factory, transcription_service=None))

    db_session.refresh(stale)
    db_session.refresh(fresh)
    assert stale.status == "failed"      # left alone — fresh sibling already covers this lane
    assert fresh.status == "completed"   # the manually-created job dispatched normally


def test_reset_stuck_llm_jobs_preserves_attempts_count(db_session):
    """attempts was already incremented at claim time (llm_worker_tick) before
    the crashed await — reset_stuck_llm_jobs must not increment it again, so
    a crash-interrupted job doesn't burn an extra retry it never used."""
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "running"
    job.attempts = 1
    db_session.commit()

    reset_stuck_llm_jobs(db_session)

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.attempts == 1


def test_rerun_still_works_after_auto_retry_exhausts_max_attempts(db_session):
    """A job that auto-retried up to MAX_ATTEMPTS and landed permanently
    'failed' must remain manually rerunnable — auto-retry must never lock
    out the existing /api/jobs/{id}/rerun path."""
    user, t = _make_user_and_transcript(db_session)
    job = enqueue_llm_job(db_session, user.id, t.id, "correction", "groq", "m1")
    job.status = "failed"
    job.attempts = MAX_ATTEMPTS
    job.error = "boom"
    db_session.commit()

    fresh = rerun_llm_job(db_session, user.id, job.id)

    assert fresh.id != job.id
    assert fresh.status == "pending"
    assert fresh.attempts == 0


def test_rerun_route_works_on_permanently_failed_job(client, db_session):
    client.put("/api/providers/groq", json={"api_key": "fake-groq-key"})
    transcript_id = _upload(client).json()["id"]

    jobs = client.get("/api/jobs").json()["jobs"]
    job_id = next(j["id"] for j in jobs if j["kind"] == "correction")

    job = db_session.query(LlmJob).filter(LlmJob.id == job_id).first()
    job.status = "failed"
    job.attempts = MAX_ATTEMPTS  # simulates auto-retry having exhausted its budget
    db_session.commit()

    rerun = client.post(f"/api/jobs/{job_id}/rerun").json()
    assert rerun["job"]["status"] == "pending"
    assert rerun["job"]["id"] != job_id
