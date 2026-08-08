import os
import pytest

from database import Transcript, TranscriptionJob, User
from services.diarization import DiarizationService
from services.queue import _finalize_if_done, _cleanup_completed_chunk_files


def _make_user(db_session, username="alice"):
    u = User(username=username, password_hash="x", password_salt="y")
    db_session.add(u)
    db_session.commit()
    return u


@pytest.mark.asyncio
async def test_finalize_cleans_up_completed_chunk_files(db_session, tmp_path):
    user = _make_user(db_session)
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="processing")
    db_session.add(t)
    db_session.commit()
    chunk0 = tmp_path / "meeting_chunk0.mp3"
    chunk1 = tmp_path / "meeting_chunk1.mp3"
    chunk0.write_bytes(b"c0")
    chunk1.write_bytes(b"c1")
    for idx, path in enumerate([str(chunk0), str(chunk1)]):
        db_session.add(TranscriptionJob(
            transcript_id=t.id, chunk_index=idx, start_time=idx * 10, end_time=(idx + 1) * 10,
            audio_path=path, status="completed",
            result_json={"segments": [{"start": idx * 10, "end": (idx + 1) * 10, "text": f"hello {idx}", "speaker": None, "confidence": None}], "language": "en", "model": "whisper-large-v3"},
        ))
    db_session.commit()

    await _finalize_if_done(db_session, t.id, DiarizationService())
    db_session.refresh(t)
    assert t.status == "completed"
    assert not chunk0.exists()
    assert not chunk1.exists()


@pytest.mark.asyncio
async def test_finalize_keeps_failed_chunk_file_for_retry(db_session, tmp_path):
    user = _make_user(db_session)
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="processing")
    db_session.add(t)
    db_session.commit()
    ok_chunk = tmp_path / "a_chunk0.mp3"
    fail_chunk = tmp_path / "a_chunk1.mp3"
    ok_chunk.write_bytes(b"ok")
    fail_chunk.write_bytes(b"fail")
    db_session.add(TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0, end_time=10,
        audio_path=str(ok_chunk), status="completed",
        result_json={"segments": [{"start": 0, "end": 10, "text": "hello", "speaker": None, "confidence": None}], "language": "en", "model": "whisper-large-v3"},
    ))
    db_session.add(TranscriptionJob(
        transcript_id=t.id, chunk_index=1, start_time=10, end_time=20,
        audio_path=str(fail_chunk), status="failed",
        result_json=None, error="boom",
    ))
    db_session.commit()

    await _finalize_if_done(db_session, t.id, DiarizationService())
    db_session.refresh(t)
    assert t.status == "partial"
    assert not ok_chunk.exists()
    assert fail_chunk.exists()


@pytest.mark.asyncio
async def test_finalize_does_not_delete_shared_chunk_path(db_session, tmp_path):
    user = _make_user(db_session)
    shared = tmp_path / "shared_chunk0.mp3"
    shared.write_bytes(b"shared")
    t1 = Transcript(user_id=user.id, title="t1", filename="t1.mp3", status="processing")
    t2 = Transcript(user_id=user.id, title="t2", filename="t2.mp3", status="processing")
    db_session.add_all([t1, t2])
    db_session.commit()
    db_session.add(TranscriptionJob(
        transcript_id=t1.id, chunk_index=0, start_time=0, end_time=10,
        audio_path=str(shared), status="completed",
        result_json={"segments": [{"start": 0, "end": 10, "text": "hello", "speaker": None, "confidence": None}], "language": "en", "model": "whisper-large-v3"},
    ))
    db_session.add(TranscriptionJob(
        transcript_id=t2.id, chunk_index=0, start_time=0, end_time=10,
        audio_path=str(shared), status="pending",
        result_json=None,
    ))
    db_session.commit()

    await _finalize_if_done(db_session, t1.id, DiarizationService())
    assert shared.exists()


def test_cleanup_helper_directly(db_session, tmp_path):
    user = _make_user(db_session)
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="processing")
    db_session.add(t)
    db_session.commit()
    chunk = tmp_path / "x_chunk0.mp3"
    chunk.write_bytes(b"x")
    job = TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0, end_time=10,
        audio_path=str(chunk), status="completed",
        result_json={"segments": [{"start": 0, "end": 10, "text": "hi", "speaker": None, "confidence": None}], "language": "en", "model": "whisper-large-v3"},
    )
    db_session.add(job)
    db_session.commit()
    _cleanup_completed_chunk_files(db_session, [job])
    assert not chunk.exists()


def test_delete_transcript_removes_chunk_files(client, db_session, tmp_path):
    chunk0 = tmp_path / "del_chunk0.mp3"
    chunk1 = tmp_path / "del_chunk1.mp3"
    chunk0.write_bytes(b"c0")
    chunk1.write_bytes(b"c1")
    user = db_session.query(User).filter(User.username == "testuser").first()
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="completed", full_text="hi", audio_path=str(tmp_path / "source.mp3"))
    db_session.add(t)
    db_session.commit()
    for idx, path in enumerate([str(chunk0), str(chunk1)]):
        db_session.add(TranscriptionJob(
            transcript_id=t.id, chunk_index=idx, start_time=idx * 10, end_time=(idx + 1) * 10,
            audio_path=path, status="completed",
            result_json={"segments": [{"start": idx * 10, "end": (idx + 1) * 10, "text": "x", "speaker": None, "confidence": None}], "language": "en", "model": "whisper-large-v3"},
        ))
    db_session.commit()
    tid = t.id

    r = client.delete(f"/api/transcripts/{tid}")
    assert r.status_code == 200
    assert not chunk0.exists()
    assert not chunk1.exists()
    assert db_session.query(Transcript).filter(Transcript.id == tid).first() is None


def test_delete_transcript_keeps_shared_chunk_file(client, db_session, tmp_path):
    shared = tmp_path / "shared_chunk0.mp3"
    shared.write_bytes(b"shared")
    user = db_session.query(User).filter(User.username == "testuser").first()
    t1 = Transcript(user_id=user.id, title="t1", filename="t1.mp3", status="completed", full_text="a")
    t2 = Transcript(user_id=user.id, title="t2", filename="t2.mp3", status="completed", full_text="b")
    db_session.add_all([t1, t2])
    db_session.commit()
    db_session.add(TranscriptionJob(transcript_id=t1.id, chunk_index=0, start_time=0, end_time=10, audio_path=str(shared), status="completed", result_json={"segments": [{"start": 0, "end": 10, "text": "a", "speaker": None, "confidence": None}], "language": "en", "model": "whisper-large-v3"}))
    db_session.add(TranscriptionJob(transcript_id=t2.id, chunk_index=0, start_time=0, end_time=10, audio_path=str(shared), status="completed", result_json={"segments": [{"start": 0, "end": 10, "text": "b", "speaker": None, "confidence": None}], "language": "en", "model": "whisper-large-v3"}))
    db_session.commit()

    r = client.delete(f"/api/transcripts/{t1.id}")
    assert r.status_code == 200
    assert shared.exists()
    assert db_session.query(Transcript).filter(Transcript.id == t2.id).first() is not None


def test_delete_transcript_removes_chunk_files_even_for_failed_jobs(client, db_session, tmp_path):
    chunk_ok = tmp_path / "f_chunk0.mp3"
    chunk_fail = tmp_path / "f_chunk1.mp3"
    chunk_ok.write_bytes(b"ok")
    chunk_fail.write_bytes(b"fail")
    user = db_session.query(User).filter(User.username == "testuser").first()
    t = Transcript(user_id=user.id, title="t", filename="t.mp3", status="partial", full_text="hi")
    db_session.add(t)
    db_session.commit()
    db_session.add(TranscriptionJob(transcript_id=t.id, chunk_index=0, start_time=0, end_time=10, audio_path=str(chunk_ok), status="completed", result_json={"segments": [{"start": 0, "end": 10, "text": "hi", "speaker": None, "confidence": None}], "language": "en", "model": "whisper-large-v3"}))
    db_session.add(TranscriptionJob(transcript_id=t.id, chunk_index=1, start_time=10, end_time=20, audio_path=str(chunk_fail), status="failed", result_json=None))
    db_session.commit()

    r = client.delete(f"/api/transcripts/{t.id}")
    assert r.status_code == 200
    assert not chunk_ok.exists()
    assert not chunk_fail.exists()
