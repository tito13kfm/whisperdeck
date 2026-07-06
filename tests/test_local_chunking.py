"""Local providers routed through the chunk pipeline for long recordings:
routing rules, budget bypass, serial dispatch, and the backend model cache."""
import asyncio
import io
import sys
import types
from unittest.mock import AsyncMock, MagicMock, patch

from database import Transcript, TranscriptionJob, User


def _post_file(client, provider="moonshine", name="long_meeting.wav"):
    return client.post(
        "/api/transcribe",
        files={"file": (name, io.BytesIO(b"fake"), "audio/wav")},
        data={"provider": provider},
    )


def _fake_chunks(n):
    return [
        {"index": i, "path": f"fake_chunk_{i}.mp3", "start_time": i * 300.0, "end_time": (i + 1) * 300.0}
        for i in range(n)
    ]


async def _stub_transcribe(db, user_id, **kwargs):
    t = Transcript(user_id=user_id, title="t", filename="f.wav", status="completed", full_text="hi")
    db.add(t)
    db.commit()
    return t


def test_long_local_file_goes_through_chunk_pipeline(client):
    with patch("app.get_audio_duration", return_value=1800.0), \
         patch("app.transcode_for_upload", AsyncMock(side_effect=lambda path, *a, **k: path)), \
         patch("app.chunk_audio", AsyncMock(return_value=_fake_chunks(6))) as fake_chunk, \
         patch("os.path.getsize", return_value=1_000_000), \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)) as sync_call:
        r = _post_file(client)

    assert r.status_code == 200
    body = r.json()
    assert body["status"] == "processing"
    assert body["job_progress"]["total"] == 6
    assert body["duration_seconds"] == 1800.0
    sync_call.assert_not_awaited()
    # chunk target derived from duration: bytes/sec × LOCAL_CHUNK_SECONDS
    target = fake_chunk.await_args.kwargs["target_chunk_bytes"]
    assert target == int(1_000_000 / 1800.0 * 300)


def test_short_local_file_keeps_sync_path(client):
    with patch("app.get_audio_duration", return_value=90.0), \
         patch("app.chunk_audio", AsyncMock()) as fake_chunk, \
         patch("app.transcription_service.transcribe", AsyncMock(side_effect=_stub_transcribe)) as sync_call:
        r = _post_file(client)

    assert r.status_code == 200
    fake_chunk.assert_not_awaited()
    sync_call.assert_awaited_once()


def test_has_budget_bypasses_local_providers(db_session):
    from services.queue import has_budget
    # enormous request that would blow any hosted budget
    assert has_budget(db_session, user_id=1, provider="moonshine", additional_seconds=10_000_000)
    assert has_budget(db_session, user_id=1, provider="builtin", additional_seconds=10_000_000)


def test_reset_stuck_transcription_jobs_fails_running_and_leaves_others_alone(db_session):
    from services.queue import reset_stuck_transcription_jobs

    user = User(username="stuck", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.wav", status="processing", provider="moonshine", model="base")
    db_session.add(t)
    db_session.commit()
    running = TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0.0, end_time=300.0,
        audio_path="c0.mp3", status="running", attempts=1,
    )
    pending = TranscriptionJob(
        transcript_id=t.id, chunk_index=1, start_time=300.0, end_time=600.0,
        audio_path="c1.mp3", status="pending",
    )
    completed = TranscriptionJob(
        transcript_id=t.id, chunk_index=2, start_time=600.0, end_time=900.0,
        audio_path="c2.mp3", status="completed",
    )
    db_session.add_all([running, pending, completed])
    db_session.commit()

    count = reset_stuck_transcription_jobs(db_session)

    assert count == 1
    db_session.refresh(running)
    db_session.refresh(pending)
    db_session.refresh(completed)
    assert running.status == "failed"
    assert running.error == "Interrupted by server restart"
    assert running.attempts == 1  # not double-counted — the crashed attempt was already recorded
    assert pending.status == "pending"
    assert completed.status == "completed"


def test_dismiss_transcript_queue_entry_requires_terminal_status(db_session):
    from services.queue import dismiss_transcript_queue_entry

    user = User(username="dismisser", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.wav", status="processing")
    db_session.add(t)
    db_session.commit()

    try:
        dismiss_transcript_queue_entry(db_session, user.id, t.id)
        assert False, "expected ValueError while still processing"
    except ValueError:
        pass

    t.status = "completed"
    db_session.commit()
    dismissed = dismiss_transcript_queue_entry(db_session, user.id, t.id)
    assert dismissed.queue_dismissed is True


def test_dismiss_transcript_queue_entry_scoped_to_owner(db_session):
    from services.queue import dismiss_transcript_queue_entry

    user = User(username="dismisser2", password_hash="x", password_salt="y")
    other = User(username="dismisser3", password_hash="x", password_salt="y")
    db_session.add_all([user, other])
    db_session.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.wav", status="completed")
    db_session.add(t)
    db_session.commit()

    try:
        dismiss_transcript_queue_entry(db_session, other.id, t.id)
        assert False, "expected LookupError for another user's transcript"
    except LookupError:
        pass

    try:
        dismiss_transcript_queue_entry(db_session, user.id, 999999)
        assert False, "expected LookupError for a nonexistent transcript"
    except LookupError:
        pass


def test_clear_finished_transcript_queue_entries_only_touches_terminal_undismissed_for_owner(db_session):
    from services.queue import clear_finished_transcript_queue_entries

    user = User(username="clearer", password_hash="x", password_salt="y")
    other = User(username="clearer2", password_hash="x", password_salt="y")
    db_session.add_all([user, other])
    db_session.commit()
    done = Transcript(user_id=user.id, title="done", filename="d.wav", status="completed")
    processing = Transcript(user_id=user.id, title="proc", filename="p.wav", status="processing")
    already_dismissed = Transcript(user_id=user.id, title="already", filename="a.wav", status="failed", queue_dismissed=True)
    other_done = Transcript(user_id=other.id, title="other", filename="o.wav", status="completed")
    db_session.add_all([done, processing, already_dismissed, other_done])
    db_session.commit()

    count = clear_finished_transcript_queue_entries(db_session, user.id)

    assert count == 1
    db_session.refresh(done)
    db_session.refresh(processing)
    db_session.refresh(other_done)
    assert done.queue_dismissed is True
    assert processing.queue_dismissed is False
    assert other_done.queue_dismissed is False  # another user's transcript untouched


def test_retry_failed_chunks_clears_queue_dismissed(db_session):
    from services.queue import retry_failed_chunks

    user = User(username="retrier", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.wav", status="failed", queue_dismissed=True)
    db_session.add(t)
    db_session.commit()
    db_session.add(TranscriptionJob(
        transcript_id=t.id, chunk_index=0, start_time=0.0, end_time=300.0,
        audio_path="c0.mp3", status="failed", attempts=3,
    ))
    db_session.commit()

    retry_failed_chunks(db_session, t.id)

    db_session.refresh(t)
    assert t.status == "processing"
    assert t.queue_dismissed is False


def test_local_chunks_dispatch_serially(db_session):
    from services.queue import queue_worker_tick

    user = User(username="serial", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.wav", status="processing", provider="moonshine", model="base")
    db_session.add(t)
    db_session.commit()
    for i in range(3):
        db_session.add(TranscriptionJob(
            transcript_id=t.id, chunk_index=i, start_time=i * 300.0, end_time=(i + 1) * 300.0,
            audio_path=f"c{i}.mp3", status="pending",
        ))
    db_session.commit()

    ran = []

    async def fake_run(db, job, *a, **k):
        ran.append(job.chunk_index)
        job.status = "completed"
        job.result_json = {"segments": [], "full_text": "", "language": "en", "model": "base"}
        db.commit()

    class _NoClose:
        def __init__(self, db): self._db = db
        def __getattr__(self, name):
            if name == "close": return lambda: None
            return getattr(self._db, name)

    with patch("services.queue._run_chunk_job", AsyncMock(side_effect=fake_run)), \
         patch("services.queue._finalize_if_done", AsyncMock()):
        asyncio.run(queue_worker_tick(lambda: _NoClose(db_session), diarization_service=None))

    assert ran == [0]  # serial: exactly one local chunk per tick


def test_moonshine_transcriber_cached_across_provider_instances():
    from backends import moonshine as moonshine_mod
    from backends.moonshine import MoonshineProvider

    moonshine_mod._TRANSCRIBER_CACHE.clear()
    fake_lib = types.ModuleType("moonshine_voice")
    fake_lib.string_to_model_arch = MagicMock(return_value="ARCH")
    fake_lib.get_model_for_language = MagicMock(return_value=("/fake/path", "ARCH"))
    fake_lib.model_arch_to_string = MagicMock(return_value="base")
    fake_lib.Transcriber = MagicMock()

    with patch.dict(sys.modules, {"moonshine_voice": fake_lib}):
        a = MoonshineProvider({"default_model": "base"})
        b = MoonshineProvider({"default_model": "base"})
        ta = a._get_transcriber()
        tb = b._get_transcriber()

    assert ta is tb
    assert fake_lib.Transcriber.call_count == 1
    moonshine_mod._TRANSCRIBER_CACHE.clear()
