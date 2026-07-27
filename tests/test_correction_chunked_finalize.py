from unittest.mock import AsyncMock, patch

import pytest

from database import ProviderConfig, Transcript, TranscriptionJob, User
from services.diarization import DiarizationService
from services.queue import _finalize_if_done


def _setup_completed_chunks(db_session, auto_correct=True, with_groq_key=True, correction_provider=None):
    user = User(username="alice", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    if not auto_correct or correction_provider:
        settings = {"auto_correct": auto_correct}
        if correction_provider:
            settings["correction_provider"] = correction_provider
        user.settings = settings
        db_session.commit()

    if with_groq_key:
        db_session.add(ProviderConfig(user_id=user.id, name="groq", api_key="fake-groq-key"))
        db_session.commit()

    transcript = Transcript(user_id=user.id, title="t", filename="f.mp3", status="processing")
    db_session.add(transcript)
    db_session.commit()

    db_session.add(TranscriptionJob(
        transcript_id=transcript.id, chunk_index=0, start_time=0, end_time=10,
        audio_path="chunk0.mp3", status="completed",
        result_json={
            "segments": [{"start": 0, "end": 5, "text": "hello world", "speaker": None, "confidence": None}],
            "language": "en", "model": "whisper-large-v3",
        },
    ))
    db_session.commit()
    return transcript


@pytest.mark.asyncio
async def test_finalize_enqueues_correction_job_when_enabled(db_session):
    from database import LlmJob

    transcript = _setup_completed_chunks(db_session, with_groq_key=False)
    await _finalize_if_done(db_session, transcript.id, DiarizationService())

    db_session.refresh(transcript)
    assert transcript.status == "completed"
    job = db_session.query(LlmJob).filter(LlmJob.transcript_id == transcript.id).first()
    assert job is not None
    assert job.kind == "correction"
    assert job.status == "pending"
    assert job.provider == "local_llm"


@pytest.mark.asyncio
async def test_finalize_skips_correction_when_setting_disabled(db_session):
    from database import LlmJob

    transcript = _setup_completed_chunks(db_session, auto_correct=False)
    await _finalize_if_done(db_session, transcript.id, DiarizationService())

    kinds = [
        k for (k,) in db_session.query(LlmJob.kind)
        .filter(LlmJob.transcript_id == transcript.id).all()
    ]
    assert "correction" not in kinds
    assert "classify_intent" not in kinds
    assert "tagging" in kinds  # issue #171: fires regardless of auto_correct


@pytest.mark.asyncio
async def test_finalize_records_skip_when_provider_key_missing(db_session):
    """No key for the configured correction provider: the job lands as
    'failed' with the skip reason (visible + rerunnable on the Queue screen)
    and the transcript's corrected tab can explain itself."""
    from database import LlmJob

    transcript = _setup_completed_chunks(db_session, with_groq_key=False, correction_provider="groq")
    await _finalize_if_done(db_session, transcript.id, DiarizationService())

    job = db_session.query(LlmJob).filter(LlmJob.transcript_id == transcript.id).first()
    assert job is not None
    assert job.status == "failed"
    assert "auto-correct skipped: no groq API key" in job.error
    db_session.refresh(transcript)
    assert "auto-correct skipped: no groq API key" in (transcript.correction_error or "")


@pytest.mark.asyncio
async def test_finalize_enqueues_tagging_job_for_meeting(db_session):
    """Tagging fires for meeting/dictation kinds via the chunked finalize
    path, in addition to the inline path in app.py — issue #171. Without
    this, long recordings (which always take the chunked path) would
    never get tagged. Belt-and-braces with the inline-path test."""
    from database import LlmJob
    transcript = _setup_completed_chunks(db_session, with_groq_key=False)
    await _finalize_if_done(db_session, transcript.id, DiarizationService())

    db_session.refresh(transcript)
    tagging = (
        db_session.query(LlmJob)
        .filter(LlmJob.transcript_id == transcript.id, LlmJob.kind == "tagging")
        .first()
    )
    assert tagging is not None
    assert tagging.status in ("pending", "failed")  # keyless path lands failed
    assert tagging.provider == "local_llm"


@pytest.mark.asyncio
async def test_finalize_enqueues_tagging_job_for_voice_note(db_session):
    """Voice-note transcripts also get tagged — the auto-enqueue helper is
    kind-agnostic and must fire from the voice_note branch in
    services/queue.py:_finalize_if_done, not just the meeting/dictation
    branch. Long voice-note recordings (above LOCAL_CHUNK_SECONDS) take
    this path; missing it would silently skip tagging for those."""
    from database import LlmJob
    user = User(username="vn", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.add(ProviderConfig(user_id=user.id, name="groq", api_key="k"))
    db_session.commit()
    transcript = Transcript(
        user_id=user.id, title="vn", filename="vn.mp3", kind="voice_note", status="processing",
    )
    db_session.add(transcript)
    db_session.commit()
    db_session.add(TranscriptionJob(
        transcript_id=transcript.id, chunk_index=0, start_time=0, end_time=10,
        audio_path="chunk0.mp3", status="completed",
        result_json={
            "segments": [{"start": 0, "end": 5, "text": "x", "speaker": None, "confidence": None}],
            "language": "en", "model": "whisper-large-v3",
        },
    ))
    db_session.commit()
    await _finalize_if_done(db_session, transcript.id, DiarizationService())

    db_session.refresh(transcript)
    tagging = (
        db_session.query(LlmJob)
        .filter(LlmJob.transcript_id == transcript.id, LlmJob.kind == "tagging")
        .first()
    )
    assert tagging is not None
    assert tagging.status == "pending"