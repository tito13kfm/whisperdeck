from unittest.mock import AsyncMock, patch

import pytest

from database import ProviderConfig, Transcript, TranscriptionJob, User
from services.diarization import DiarizationService
from services.queue import _finalize_if_done


def _setup_completed_chunks(db_session, auto_correct=True, with_groq_key=True):
    user = User(username="alice", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    if not auto_correct:
        user.settings = {"auto_correct": False}
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
async def test_finalize_runs_correction_when_enabled(db_session):
    transcript = _setup_completed_chunks(db_session)

    async def _fake_correct(db, t, api_key, provider_name="groq", model="llama-3.3-70b-versatile", **kwargs):
        t.corrected_text = "Hello world."
        t.correction_model = f"{provider_name}/{model}"
        db.commit()

    # Patch the inner LLM call so run_auto_correction's settings/key
    # resolution still runs for real against the groq ProviderConfig.
    with patch("services.correction.correct_transcript", side_effect=_fake_correct):
        await _finalize_if_done(db_session, transcript.id, DiarizationService())

    db_session.refresh(transcript)
    assert transcript.status == "completed"
    assert transcript.corrected_text == "Hello world."


@pytest.mark.asyncio
async def test_finalize_skips_correction_when_setting_disabled(db_session):
    transcript = _setup_completed_chunks(db_session, auto_correct=False)
    fake_auto = AsyncMock()

    with patch("services.correction.run_auto_correction", fake_auto):
        await _finalize_if_done(db_session, transcript.id, DiarizationService())

    fake_auto.assert_not_awaited()


@pytest.mark.asyncio
async def test_finalize_records_skip_when_provider_key_missing(db_session):
    """No key for the configured correction provider: no LLM call happens and
    the skip reason lands on the transcript for the corrected tab to show."""
    transcript = _setup_completed_chunks(db_session, with_groq_key=False)
    fake_post = AsyncMock()

    with patch("httpx.AsyncClient.post", fake_post):
        await _finalize_if_done(db_session, transcript.id, DiarizationService())

    fake_post.assert_not_awaited()
    db_session.refresh(transcript)
    assert "auto-correct skipped: no groq API key" in (transcript.correction_error or "")
