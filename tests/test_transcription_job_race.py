"""Regression tests for the queue.py CAS port (issue #402).

Every TranscriptionJob status write that can race with another session now
goes through services.job_transitions.transition (a single
``UPDATE ... WHERE status IN (expect)``). These tests pin that the stale
writer no-ops instead of clobbering a job that moved past ``failed`` while
the stale writer was queued on sqlite's busy_timeout.

Shape mirrors tests/test_llm_job_finish_race.py — a competing transition
committed from a genuinely separate DB connection, landed inside the window
between the stale writer's read and its CAS write.
"""

import asyncio
import datetime
from unittest.mock import patch

from sqlalchemy import text

from database import TranscriptionJob, Transcript, User, utcnow_naive as _real_utcnow_naive
from services.job_transitions import transition as _real_transition
from services.queue import queue_worker_tick, retry_failed_chunks


def _user(db_session, name="racer"):
    user = User(username=name, password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    return user


def _transcript(db_session, user, status="processing"):
    t = Transcript(user_id=user.id, title="t", filename="f.wav", status=status)
    db_session.add(t)
    db_session.commit()
    return t


def _complete_from_another_connection(engine, job_id):
    with engine.connect() as conn:
        conn.execute(
            text("UPDATE transcription_jobs SET status='completed', result_json=:r WHERE id=:i"),
            {"i": job_id, "r": '{"segments": [], "full_text": "done"}'},
        )
        conn.commit()


def _cancel_from_another_connection(engine, job_id):
    with engine.connect() as conn:
        conn.execute(text("UPDATE transcription_jobs SET status='cancelled' WHERE id=:i"), {"i": job_id})
        conn.commit()


class _NoClose:
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._db, name)


# ── direct transition() unit tests ──────────────────────────────────────────


def test_transition_succeeds_when_expect_matches(db_session):
    user = _user(db_session, "t1")
    t = _transcript(db_session, user)
    job = TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0, end_time=10,
        audio_path="c0.mp3", status="failed", attempts=1,
        error="blip", updated_at=_real_utcnow_naive() - datetime.timedelta(seconds=1000),
    )
    db_session.add(job)
    db_session.commit()

    ok = _real_transition(db_session, TranscriptionJob, job.id, "pending", expect=("failed",), error=None)
    assert ok is True
    db_session.refresh(job)
    assert job.status == "pending"
    assert job.error is None


def test_transition_noops_when_expect_mismatches(db_session):
    user = _user(db_session, "t2")
    t = _transcript(db_session, user)
    job = TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0, end_time=10,
        audio_path="c0.mp3", status="completed", result_json={"segments": []},
    )
    db_session.add(job)
    db_session.commit()

    ok = _real_transition(db_session, TranscriptionJob, job.id, "pending", expect=("failed",), error=None)
    assert ok is False
    db_session.refresh(job)
    assert job.status == "completed"


# ── sweep / retry race — stale resurrecter must not revert a completed job ─


def test_retry_failed_chunks_loses_race_to_completion_from_another_connection(db_session):
    """retry_failed_chunks loaded a job as failed, but a concurrent writer
    already moved it to completed — the stale CAS must no-op."""
    user = _user(db_session, "retry-racer")
    t = _transcript(db_session, user)
    job = TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0, end_time=10,
        audio_path="c0.mp3", status="failed", attempts=1, error="blip",
    )
    db_session.add(job)
    db_session.commit()

    engine = db_session.get_bind()
    fired = {"done": False}

    def hook_utcnow():
        if not fired["done"]:
            fired["done"] = True
            _complete_from_another_connection(engine, job.id)
        return _real_utcnow_naive()

    with patch("services.job_transitions.utcnow_naive", side_effect=hook_utcnow):
        count = retry_failed_chunks(db_session, t.id)

    assert count == 0
    db_session.refresh(job)
    assert job.status == "completed"


def test_sweep_resurrection_loses_race_to_completion_from_another_connection(db_session):
    """queue_worker_tick's resurrection sweep must not revert a job that
    was already completed by a concurrent session."""
    user = _user(db_session, "sweep-racer")
    t = _transcript(db_session, user, status="processing")
    job = TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0, end_time=10,
        audio_path="c0.mp3", status="failed", attempts=1,
        error="blip",
        updated_at=_real_utcnow_naive() - datetime.timedelta(seconds=1000),
    )
    db_session.add(job)
    db_session.commit()

    engine = db_session.get_bind()
    fired = {"done": False}

    def hook_utcnow():
        if not fired["done"]:
            fired["done"] = True
            _complete_from_another_connection(engine, job.id)
        return _real_utcnow_naive()

    async def _run_noop(*a, **kw):
        pass

    factory = lambda: _NoClose(db_session)
    with patch("services.job_transitions.utcnow_naive", side_effect=hook_utcnow), \
         patch("services.queue._run_chunk_job", _run_noop), \
         patch("services.queue._finalize_if_done", _run_noop):
        asyncio.run(queue_worker_tick(factory, diarization_service=None))

    db_session.refresh(job)
    assert job.status == "completed"


def test_cancel_loses_race_to_already_running(db_session):
    """A pending job that moved to running before cancel's CAS must stay running."""
    from services.queue import cancel_transcript_jobs

    user = _user(db_session, "cancel-racer")
    t = _transcript(db_session, user, status="processing")
    job = TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0, end_time=10,
        audio_path="c0.mp3", status="pending",
    )
    db_session.add(job)
    db_session.commit()

    engine = db_session.get_bind()
    fired = {"done": False}

    def hook_utcnow():
        if not fired["done"]:
            fired["done"] = True
            with engine.connect() as conn:
                conn.execute(text("UPDATE transcription_jobs SET status='running' WHERE id=:i"), {"i": job.id})
                conn.commit()
        return _real_utcnow_naive()

    with patch("services.job_transitions.utcnow_naive", side_effect=hook_utcnow):
        count = cancel_transcript_jobs(db_session, t.id)

    assert count == 0
    db_session.refresh(job)
    assert job.status == "running"


def test_run_chunk_job_loses_claim_to_concurrent_cancel(db_session):
    """_run_chunk_job's pending->running claim must not clobber a cancel that
    committed between dispatch selection and the claim itself, and must not
    dispatch to the provider when the claim is lost."""
    from services.queue import _run_chunk_job

    user = _user(db_session, "claim-racer")
    t = _transcript(db_session, user, status="processing")
    job = TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0, end_time=10,
        audio_path="c0.mp3", status="pending", attempts=0,
    )
    db_session.add(job)
    db_session.commit()

    engine = db_session.get_bind()
    fired = {"done": False}

    def hook_utcnow():
        if not fired["done"]:
            fired["done"] = True
            _cancel_from_another_connection(engine, job.id)
        return _real_utcnow_naive()

    provider_calls = {"count": 0}

    def _track_provider(*a, **kw):
        provider_calls["count"] += 1
        raise AssertionError("provider must not be dispatched when the claim is lost")

    with patch("services.job_transitions.utcnow_naive", side_effect=hook_utcnow), \
         patch("services.queue.get_provider", side_effect=_track_provider):
        asyncio.run(_run_chunk_job(db_session, job, {}, "openai", "en", asyncio.Semaphore(1)))

    assert provider_calls["count"] == 0
    db_session.refresh(job)
    assert job.status == "cancelled"
    assert job.attempts == 0


def test_nonconflicting_retry_still_succeeds(db_session):
    """Control: without a race, retry_failed_chunks still resurrects."""
    user = _user(db_session, "retry-ok")
    t = _transcript(db_session, user, status="failed")
    t.queue_dismissed = True
    db_session.commit()
    job = TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0, end_time=10,
        audio_path="c0.mp3", status="failed", attempts=1, error="blip",
    )
    db_session.add(job)
    db_session.commit()

    count = retry_failed_chunks(db_session, t.id)
    assert count == 1
    db_session.refresh(job)
    assert job.status == "pending"
    assert job.attempts == 0
    assert job.error is None
    db_session.refresh(t)
    assert t.status == "processing"
