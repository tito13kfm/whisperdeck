"""voice_match background job: relabels segments against the roster,
leaves low-confidence segments untouched, tolerates per-segment failures."""
import asyncio
import numpy as np
from unittest.mock import patch

from database import Transcript, User, VoiceProfile
from services.llm_jobs import enqueue_llm_job, run_llm_job
from services.voice_id import voice_id_service


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


def _enrolled_profile(db_session, user, name="Alice"):
    profile = VoiceProfile(
        user_id=user.id, name=name, embedding=[0.1, 0.2, 0.3],
        embedding_model="test", sample_count=1,
    )
    db_session.add(profile)
    db_session.commit()
    return profile


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
    _enrolled_profile(db_session, user)
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


def test_voice_match_runs_real_identify_through_executor(db_session, tmp_path, monkeypatch):
    """Only extraction and embedding extraction are stubbed — voice_id_service.identify()
    itself runs for real (its own db.query included), through the run_in_executor wrap
    added in services/llm_jobs.py, against the same file-backed sqlite db_session used
    everywhere else (check_same_thread=False, matching production)."""
    user = _user(db_session)
    _enrolled_profile(db_session, user)
    segments = [{"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"}]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    async def fake_extract(audio_path, clips, output_dir):
        return str(tmp_path / "clip.wav")

    monkeypatch.setattr(voice_id_service, "_extract_embedding", lambda path: np.array([0.1, 0.2, 0.3]))

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", fake_extract):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    db_session.refresh(t)
    assert job.status == "completed"
    assert t.segments[0]["speaker"] == "Alice"


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


def test_voice_match_fails_fast_with_empty_roster(db_session, tmp_path):
    """No VoiceProfile rows (or none with an embedding) for this user — the
    job should fail before extracting audio for a single segment."""
    user = _user(db_session)
    segments = [
        {"start": 0.0, "end": 1.0, "text": "hi", "speaker": "SPEAKER_00"},
        {"start": 1.0, "end": 2.0, "text": "bye", "speaker": "SPEAKER_01"},
    ]
    t = _transcript_with_segments(db_session, user, tmp_path, segments)
    job = enqueue_llm_job(db_session, user.id, t.id, "voice_match", "", "")
    job.status = "running"
    db_session.commit()

    def fail_if_called(*args, **kwargs):
        raise AssertionError("extract_clips_concat should not be called with an empty roster")

    factory = lambda: _NoCloseSession(db_session)
    with patch("services.llm_jobs.extract_clips_concat", fail_if_called):
        asyncio.run(run_llm_job(factory, job.id, transcription_service=None))

    db_session.refresh(job)
    assert job.status == "failed"
    assert job.error == "No enrolled voices with clips — add a clip to a roster profile first"


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
    _enrolled_profile(db_session, user)
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


def test_voice_match_route_enqueues_job(client, db_session, tmp_path):
    from database import User as _User
    user = db_session.query(_User).filter(_User.username == "testuser").first()
    if not user:
        user = _User(username="testuser", password_hash="x", password_salt="y")
        db_session.add(user)
        db_session.commit()
    t = _transcript_with_segments(db_session, user, tmp_path,
                                   [{"start": 0, "end": 1, "text": "hi", "speaker": "S"}])
    r = client.post(f"/api/transcripts/{t.id}/voice-match")
    assert r.status_code == 200
    assert r.json()["job"]["kind"] == "voice_match"
    assert r.json()["job"]["status"] == "pending"


def test_voice_match_route_400_without_stored_audio(client, db_session):
    from database import User as _User, Transcript as _Transcript
    user = db_session.query(_User).filter(_User.username == "testuser").first()
    if not user:
        user = _User(username="testuser", password_hash="x", password_salt="y")
        db_session.add(user)
        db_session.commit()
    t = _Transcript(user_id=user.id, title="n", filename="n.mp3", status="completed", full_text="x")
    db_session.add(t)
    db_session.commit()
    r = client.post(f"/api/transcripts/{t.id}/voice-match")
    assert r.status_code == 400


def test_transcript_serialization_includes_voice_match_job(client, db_session, tmp_path):
    from database import User as _User
    user = db_session.query(_User).filter(_User.username == "testuser").first()
    if not user:
        user = _User(username="testuser", password_hash="x", password_salt="y")
        db_session.add(user)
        db_session.commit()
    t = _transcript_with_segments(db_session, user, tmp_path,
                                   [{"start": 0, "end": 1, "text": "hi", "speaker": "S"}])
    client.post(f"/api/transcripts/{t.id}/voice-match")
    r = client.get(f"/api/transcripts/{t.id}")
    assert r.json()["voice_match_job"]["kind"] == "voice_match"
