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
