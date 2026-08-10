"""Regression for #328: partial transcripts must not re-finalize on every chunk retry
with byte-identical text, re-firing the LLM enqueue volley (duplicate paid calls)
and wiping relabel history."""
import asyncio
import datetime
from unittest.mock import AsyncMock

import pytest

from database import LlmJob, ProviderConfig, Transcript, TranscriptionJob, User, utcnow_naive
from services.diarization import DiarizationService
from services.queue import _finalize_if_done, queue_worker_tick
from services.relabel import record_relabel


def _setup_partial_transcript(db_session, *, with_key=True):
    user = User(username="partial328", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    if with_key:
        db_session.add(ProviderConfig(user_id=user.id, name="groq", api_key="fake-key"))
        db_session.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.mp3", status="processing", kind="meeting")
    db_session.add(t)
    db_session.commit()
    db_session.add(TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0, end_time=10,
        audio_path="chunk0.mp3", status="completed",
        result_json={
            "segments": [{"start": 0, "end": 5, "text": "hello world", "speaker": "Speaker A", "confidence": 0.9}],
            "language": "en", "model": "whisper-large-v3",
        },
    ))
    db_session.add(TranscriptionJob(
        transcript_id=t.id, chunk_index=1, start_time=10, end_time=20,
        audio_path="chunk1.mp3", status="failed",
        result_json=None,
        attempts=1,
        error="transient",
        updated_at=utcnow_naive(),
    ))
    db_session.commit()
    return user, t


@pytest.mark.asyncio
async def test_refinalize_same_text_does_not_duplicate_llm_jobs(db_session):
    """First finalize creates the LLM volley. A second finalize with identical
    merged text (failed chunk still failed, no new segments) must NOT create
    duplicate LlmJobs. Mark first volley's jobs completed to simulate the
    backoff-window case where dedupe via ACTIVE_STATUSES would otherwise miss."""
    from database import LlmJob as LlmJobModel
    user, t = _setup_partial_transcript(db_session, with_key=False)

    await _finalize_if_done(db_session, t.id, DiarizationService())
    db_session.refresh(t)
    assert t.status == "partial"
    first_kinds = sorted(k for (k,) in db_session.query(LlmJobModel.kind).filter(LlmJobModel.transcript_id == t.id).all())
    assert "correction" in first_kinds or "tagging" in first_kinds
    first_count = db_session.query(LlmJobModel).filter(LlmJobModel.transcript_id == t.id).count()
    assert first_count > 0

    # Simulate jobs completing inside the 10s backoff window (common case).
    for job in db_session.query(LlmJobModel).filter(LlmJobModel.transcript_id == t.id).all():
        job.status = "completed"
    db_session.commit()

    # Second finalize with byte-identical text (B still failed, no new segments).
    await _finalize_if_done(db_session, t.id, DiarizationService())
    db_session.refresh(t)
    assert t.status == "partial"
    second_count = db_session.query(LlmJobModel).filter(LlmJobModel.transcript_id == t.id).count()
    assert second_count == first_count, f"duplicate LLM jobs created: {first_count} -> {second_count}"


@pytest.mark.asyncio
async def test_refinalize_same_text_does_not_wipe_relabel_history(db_session):
    """A user relabel between two identical re-finalizations must survive.
    clear_relabel_history must be gated on content change, and segments must
    not be overwritten with non-diarized merge results (Oracle #328)."""
    user, t = _setup_partial_transcript(db_session, with_key=False)
    await _finalize_if_done(db_session, t.id, DiarizationService())
    db_session.refresh(t)
    assert t.status == "partial"

    # Simulate user relabel after first finalize.
    diarized_segments = [{"start": 0, "end": 5, "text": "hello world", "speaker": "Speaker A"}]
    t.segments = diarized_segments
    db_session.commit()
    record_relabel(db_session, t, "manual", [(0, "Speaker A")], description="user fix")
    db_session.refresh(t)
    # Confirm history exists.
    from database import RelabelHistory
    hist_count_before = db_session.query(RelabelHistory).filter(RelabelHistory.transcript_id == t.id).count()
    assert hist_count_before == 1

    # Second identical finalize must NOT wipe it and must NOT overwrite segments.
    await _finalize_if_done(db_session, t.id, DiarizationService())
    db_session.refresh(t)
    hist_count_after = db_session.query(RelabelHistory).filter(RelabelHistory.transcript_id == t.id).count()
    assert hist_count_after == 1, "relabel history was wiped on identical re-finalize"
    assert t.segments == diarized_segments, "diarized segments were overwritten on identical re-finalize"


@pytest.mark.asyncio
async def test_refinalize_with_new_content_does_fire_side_effects(db_session):
    """When the retry actually succeeds and merged text grows, the side effect
    volley IS desirable — correction should re-run on the now-longer text."""
    from database import LlmJob as LlmJobModel
    user, t = _setup_partial_transcript(db_session, with_key=False)

    await _finalize_if_done(db_session, t.id, DiarizationService())
    db_session.refresh(t)
    assert t.status == "partial"
    first_full = t.full_text
    first_count = db_session.query(LlmJobModel).filter(LlmJobModel.transcript_id == t.id).count()
    # Complete first volley jobs so dedupe doesn't hide a second volley.
    for job in db_session.query(LlmJobModel).filter(LlmJobModel.transcript_id == t.id).all():
        job.status = "completed"
    db_session.commit()

    # Simulate retry success: failed chunk now completed with new text.
    failed = db_session.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == t.id, TranscriptionJob.chunk_index == 1).first()
    failed.status = "completed"
    failed.result_json = {
        "segments": [{"start": 10, "end": 15, "text": "second chunk here", "speaker": None, "confidence": 0.9}],
        "language": "en", "model": "whisper-large-v3",
    }
    db_session.commit()

    await _finalize_if_done(db_session, t.id, DiarizationService())
    db_session.refresh(t)
    assert t.status == "completed"
    assert t.full_text != first_full
    assert "second chunk here" in (t.full_text or "")
    second_count = db_session.query(LlmJobModel).filter(LlmJobModel.transcript_id == t.id).count()
    assert second_count > first_count, f"expected side effects to re-fire on new content: {first_count} -> {second_count}"


@pytest.mark.asyncio
async def test_auto_retry_resets_transcript_status_to_processing(db_session):
    """queue_worker_tick's automatic retry pass must mirror retry_failed_chunks:
    resurrecting a failed job also flips transcript.status back to processing."""
    from database import Transcript as TModel
    user, t = _setup_partial_transcript(db_session, with_key=False)
    # Finalize to partial first.
    await _finalize_if_done(db_session, t.id, DiarizationService())
    db_session.refresh(t)
    assert t.status == "partial"
    # Make the failed job retry-eligible (backoff elapsed).
    failed = db_session.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == t.id, TranscriptionJob.chunk_index == 1).first()
    failed.updated_at = utcnow_naive() - datetime.timedelta(seconds=100)
    db_session.commit()

    # queue_worker_tick closes its session in a finally block. Wrap our
    # shared db_session so close() is a no-op, mirroring test_llm_jobs.py's
    # _NoCloseSession pattern.
    class _NoCloseSession:
        def __init__(self, db):
            self._db = db
        def __getattr__(self, name):
            if name == "close":
                return lambda: None
            return getattr(self._db, name)

    def _session_factory():
        return _NoCloseSession(db_session)

    from services import queue as queue_mod
    orig_run = queue_mod._run_chunk_job

    async def _stub_run(db, job, *a, **k):
        job.status = "pending"
        db.commit()

    queue_mod._run_chunk_job = _stub_run
    try:
        fake_diar = AsyncMock()
        fake_diar.diarize_and_merge = AsyncMock(return_value=([], 0, "heuristic", None))
        await queue_worker_tick(_session_factory, fake_diar)
    finally:
        queue_mod._run_chunk_job = orig_run

    db_session.refresh(t)
    db_session.refresh(failed)
    assert failed.status == "pending"
    assert t.status == "processing"
    assert t.queue_dismissed is False
