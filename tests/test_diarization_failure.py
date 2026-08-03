"""Regression tests for issue #120: diarization failure in _finalize_if_done
produces visible error state (partial status, error message, diarization_method
set to 'failed') instead of silently completing without speaker labels."""
import pytest
from unittest.mock import patch

from database import Transcript, TranscriptionJob, User
from services.queue import _finalize_if_done
from services.diarization import DiarizationService


def _setup_transcript(db_session, diarize=True, audio_path="/fake/audio.mp3"):
    """Create a transcript with one completed chunk job, ready for finalize."""
    user = User(username="alice", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()

    transcript = Transcript(
        user_id=user.id,
        title="test",
        filename="test.mp3",
        status="processing",
        diarize_requested=diarize,
        audio_path=audio_path,
    )
    db_session.add(transcript)
    db_session.commit()

    db_session.add(TranscriptionJob(
        transcript_id=transcript.id, chunk_index=0,
        start_time=0, end_time=10, audio_path="chunk0.mp3",
        status="completed",
        result_json={
            "segments": [{"start": 0, "end": 5, "text": "hello world",
                           "speaker": None, "confidence": None}],
            "language": "en", "model": "whisper-large-v3",
        },
    ))
    db_session.commit()
    return transcript


@pytest.mark.asyncio
async def test_diarization_failure_sets_partial_status_and_error(db_session):
    """When diarize_and_merge raises, _finalize_if_done sets status to
    'partial', records the error, and sets diarization_method='failed'."""
    transcript = _setup_transcript(db_session)
    diar_service = DiarizationService()

    with patch.object(diar_service, "diarize_and_merge",
                      side_effect=RuntimeError("pyannote OOM")):
        await _finalize_if_done(db_session, transcript.id, diar_service)

    db_session.refresh(transcript)
    assert transcript.status == "partial", (
        f"Expected 'partial' status after diarization failure, got '{transcript.status}'"
    )
    assert transcript.error is not None
    assert "Diarization failed" in transcript.error
    assert "pyannote OOM" in transcript.error
    assert transcript.diarization_method == "failed"


@pytest.mark.asyncio
async def test_diarization_failure_keeps_segments_undiarized(db_session):
    """Segments should remain undiarized (no speaker labels) after failure."""
    transcript = _setup_transcript(db_session)
    diar_service = DiarizationService()

    with patch.object(diar_service, "diarize_and_merge",
                      side_effect=RuntimeError("diarization crash")):
        await _finalize_if_done(db_session, transcript.id, diar_service)

    db_session.refresh(transcript)
    for seg in transcript.segments:
        # Pre-diarization segments have speaker=None from result_json
        assert seg.get("speaker") is None, (
            f"Segments should not have speaker labels after diarization failure"
        )


@pytest.mark.asyncio
async def test_diarization_success_still_works(db_session):
    """Non-regression: successful diarization path should still complete
    normally with 'completed' status and speaker labels set."""
    transcript = _setup_transcript(db_session)
    diar_service = DiarizationService()

    async def fake_diarize_and_merge(*args, **kwargs):
        return [
            {"start": 0, "end": 5, "text": "hello world", "speaker": "SPEAKER_01"}
        ], 1, "heuristic"

    with patch.object(diar_service, "diarize_and_merge",
                      side_effect=fake_diarize_and_merge):
        await _finalize_if_done(db_session, transcript.id, diar_service)

    db_session.refresh(transcript)
    assert transcript.status == "completed"
    assert transcript.error is None
    assert transcript.diarization_method == "heuristic"
    assert transcript.speaker_count == 1
