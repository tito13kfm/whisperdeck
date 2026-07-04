"""voice_match background job: relabels segments against the roster,
leaves low-confidence segments untouched, tolerates per-segment failures."""
import asyncio
from unittest.mock import patch

from database import Transcript, User
from services.llm_jobs import enqueue_llm_job, run_llm_job


class _NoCloseSession:
    def __init__(self, db):
        self._db = db

    def __getattr__(self, name):
        if name == "close":
            return lambda: None
        return getattr(self._db, name)


def _user(db_session, name="matcher"):
    user = User(username=name, password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    return user


def _transcript_with_segments(db_session, user, tmp_path, segments):
    audio = tmp_path / "a.mp3"
    audio.write_bytes(b"fake")
    t = Transcript(user_id=user.id, title="d", filename="d.mp3", status="completed",
                   full_text="x", segments=segments, audio_path=str(audio))
    db_session.add(t)
    db_session.commit()
    return t


def test_voice_match_relabels_confident_segments_only(db_session, tmp_path):
    user = _user(db_session)
    segments = [
        {"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "text": "bye", "speaker": "SPEAKER_01"},
    ]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    async def fake_extract(audio_path, clips, output_dir):
        return str(tmp_path / "clip.wav")

    def fake_identify(db, user_id, audio_path, threshold=0.65):
        # first call (segment 0) matches confidently, second doesn't
        fake_identify.calls += 1
        if fake_identify.calls == 1:
            return [{"id": 1, "name": "Alice", "similarity": 0.9, "sample_count": 2}]
        return []
    fake_identify.calls = 0

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", fake_extract), \
         patch("services.llm_jobs.voice_id_service.identify", fake_identify):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "completed"
    assert t.segments[0]["speaker"] == "Alice"
    assert t.segments[1]["speaker"] == "SPEAKER_01"  # untouched, no confident match
    assert job.progress_done == 2
    assert job.progress_total == 2


def test_voice_match_fails_fast_with_no_backend(db_session, tmp_path):
    user = _user(db_session)
    segments = [{"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"}]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.voice_id_service._backend", "none"):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"
    assert "backend" in job.error.lower()


def test_voice_match_fails_when_audio_missing(db_session):
    user = _user(db_session)
    t = Transcript(user_id=user.id, title="d", filename="d.mp3", status="completed",
                   full_text="x", segments=[{"start": 0, "end": 1, "text": "hi", "speaker": "S"}],
                   audio_path="nope/missing.mp3")
    db_session.add(t)
    db_session.commit()
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    factory = lambda: _NoCloseSession(db_session)
    asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"
    assert "No stored audio" in job.error


def test_voice_match_skips_segment_on_extraction_failure_without_failing_job(db_session, tmp_path):
    user = _user(db_session)
    segments = [
        {"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "text": "bye", "speaker": "SPEAKER_01"},
    ]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    async def flaky_extract(audio_path, clips, output_dir):
        flaky_extract.calls += 1
        if flaky_extract.calls == 1:
            raise ValueError("boom")
        return str(tmp_path / "clip.wav")
    flaky_extract.calls = 0

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", flaky_extract), \
         patch("services.llm_jobs.voice_id_service.identify",
               lambda db, user_id, audio_path, threshold=0.65: [{"id": 1, "name": "Alice", "similarity": 0.9, "sample_count": 1}]):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "completed"
    assert t.segments[0]["speaker"] == "SPEAKER_00"  # extraction failed, left alone
    assert t.segments[1]["speaker"] == "Alice"
    assert "1 segment" in job.error  # skip count surfaced even though status is completed
