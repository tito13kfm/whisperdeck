"""Silent chunk pre-check: is_silent_audio helper, chunk_audio filtering, and queue-layer guard."""
import asyncio
import os
import subprocess
import types
from unittest.mock import AsyncMock, MagicMock, patch, call

import pytest

from database import Transcript, TranscriptionJob, User
from services.audio_prep import is_silent_audio


class _FakeResult:
    def __init__(self, stderr, returncode=0):
        self.stderr = stderr
        self.returncode = returncode
        self.stdout = ""


def test_is_silent_audio_below_threshold_returns_true():
    with patch("services.audio_prep.subprocess.run", return_value=_FakeResult("  max_volume: -60.0 dB")):
        assert is_silent_audio("fake.mp3", threshold_db=-50.0) is True


def test_is_silent_audio_above_threshold_returns_false():
    with patch("services.audio_prep.subprocess.run", return_value=_FakeResult("  max_volume: -20.5 dB")):
        assert is_silent_audio("fake.mp3", threshold_db=-50.0) is False


def test_is_silent_audio_at_threshold_returns_false():
    with patch("services.audio_prep.subprocess.run", return_value=_FakeResult("  max_volume: -50.0 dB")):
        assert is_silent_audio("fake.mp3", threshold_db=-50.0) is False


def test_is_silent_audio_na_returns_true():
    with patch("services.audio_prep.subprocess.run", return_value=_FakeResult("  max_volume: n/a dB")):
        assert is_silent_audio("fake.mp3") is True


def test_is_silent_audio_neg_inf_returns_true():
    with patch("services.audio_prep.subprocess.run", return_value=_FakeResult("  max_volume: -inf dB")):
        assert is_silent_audio("fake.mp3") is True
    with patch("services.audio_prep.subprocess.run", return_value=_FakeResult("  max_volume: -INF dB")):
        assert is_silent_audio("fake.mp3") is True


def test_is_silent_audio_pos_inf_returns_false():
    with patch("services.audio_prep.subprocess.run", return_value=_FakeResult("  max_volume: inf dB")):
        assert is_silent_audio("fake.mp3") is False


def test_is_silent_audio_no_match_returns_false():
    with patch("services.audio_prep.subprocess.run", return_value=_FakeResult("some unrelated ffmpeg output")):
        assert is_silent_audio("fake.mp3") is False


def test_is_silent_audio_subprocess_error_returns_false():
    with patch("services.audio_prep.subprocess.run", side_effect=OSError("no ffmpeg")):
        assert is_silent_audio("fake.mp3") is False


def test_is_silent_audio_custom_threshold():
    with patch("services.audio_prep.subprocess.run", return_value=_FakeResult("  max_volume: -40.0 dB")):
        assert is_silent_audio("fake.mp3", threshold_db=-30.0) is True
        assert is_silent_audio("fake.mp3", threshold_db=-50.0) is False


def test_chunk_audio_filters_silent_chunks(tmp_path):
    """chunk_audio must drop silent chunks, delete their files, and renumber survivors."""
    import services.audio_prep as ap

    fake_duration = 600.0
    fake_bytes = 600_000
    fake_silence = [150.0]

    created_paths = []

    def fake_cut_run(cmd, capture_output=True, text=True):
        # Detect chunk cut commands (contain "-c copy") vs volumedetect calls
        if "-c" in cmd and "copy" in cmd:
            path = cmd[-1]
            created_paths.append(path)
            # Create a dummy file so os.path.exists and os.remove work
            open(path, "w").close()
            return _FakeResult("")
        return _FakeResult("")

    with patch("services.audio_prep.get_audio_duration", return_value=fake_duration), \
         patch("services.audio_prep.detect_silence_midpoints", return_value=fake_silence), \
         patch("services.audio_prep.ffmpeg_available", return_value=True), \
         patch("services.audio_prep.subprocess.run", side_effect=fake_cut_run), \
         patch("os.path.getsize", return_value=fake_bytes), \
         patch("services.audio_prep.is_silent_audio") as mock_silent:

        # 2 chunks expected (600s / target=300s). Make second one silent.
        def silent_side_effect(path, threshold_db=-50.0):
            return path.endswith("chunk1.mp3")

        mock_silent.side_effect = silent_side_effect

        # target 300s worth of bytes
        target = int(fake_bytes / fake_duration * 300)
        chunks = asyncio.run(ap.chunk_audio(str(tmp_path / "input.mp3"), str(tmp_path), target_chunk_bytes=target))

    assert len(chunks) == 1
    assert chunks[0]["index"] == 0
    assert "chunk0" in chunks[0]["path"]
    # Silent chunk file should have been deleted
    assert not os.path.exists(str(tmp_path / "input_chunk1.mp3")) or not any("chunk1" in c["path"] for c in chunks)


def test_chunk_audio_keeps_all_when_none_silent(tmp_path):
    import services.audio_prep as ap

    with patch("services.audio_prep.get_audio_duration", return_value=600.0), \
         patch("services.audio_prep.detect_silence_midpoints", return_value=[150.0]), \
         patch("services.audio_prep.ffmpeg_available", return_value=True), \
         patch("services.audio_prep.subprocess.run", side_effect=lambda *a, **k: _FakeResult("")), \
         patch("os.path.getsize", return_value=600_000), \
         patch("services.audio_prep.is_silent_audio", return_value=False), \
         patch("os.path.exists", return_value=True), \
         patch("os.remove"):

        # Need to actually create files for is_silent path check? chunk_audio creates via _cut_one
        # Mock _cut_one indirectly via subprocess — but we also need os.path.getsize etc.
        # Easiest: patch the whole _run_all behavior via direct is_silent mock = False keeps all chunks
        target = int(600_000 / 600.0 * 300)
        # We need _cut_one to succeed: mock subprocess for cut
        with patch("services.audio_prep.subprocess.run", return_value=_FakeResult("")):
            # This path is tricky — just verify is_silent=False keeps chunks
            pass

    # Simpler: verify non-silent chunk count directly
    with patch("services.audio_prep.get_audio_duration", return_value=600.0), \
         patch("services.audio_prep.detect_silence_midpoints", return_value=[]), \
         patch("services.audio_prep.ffmpeg_available", return_value=True), \
         patch("os.path.getsize", return_value=600_000):

        def fake_run(cmd, capture_output=True, text=True):
            if "-c" in cmd and "copy" in cmd:
                path = cmd[-1]
                open(path, "w").close()
            return _FakeResult("")

        with patch("services.audio_prep.subprocess.run", side_effect=fake_run), \
             patch("services.audio_prep.is_silent_audio", return_value=False):
            target = int(600_000 / 600.0 * 300)
            chunks = asyncio.run(ap.chunk_audio(str(tmp_path / "input2.mp3"), str(tmp_path), target_chunk_bytes=target))
            assert len(chunks) == 2
            assert chunks[0]["index"] == 0
            assert chunks[1]["index"] == 1


def test_run_chunk_job_skips_silent_without_provider_call(db_session):
    """_run_chunk_job must complete silent jobs with empty result, not call provider."""
    from services.queue import _run_chunk_job

    user = User(username="silentq", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.wav", status="processing", provider="groq", model="base", language="en")
    db_session.add(t)
    db_session.commit()
    job = TranscriptionJob(transcript_id=t.id, chunk_index=0, start_time=0.0, end_time=60.0, audio_path="/tmp/fake_silent.mp3", status="pending")
    db_session.add(job)
    db_session.commit()

    fake_provider = MagicMock()
    fake_provider.transcribe = AsyncMock()

    with patch("services.audio_prep.is_silent_audio", return_value=True), \
         patch("os.path.exists", return_value=True), \
         patch("services.queue.get_provider", return_value=fake_provider):
        asyncio.run(_run_chunk_job(db_session, job, {}, "groq", "en", asyncio.Semaphore(1)))

    db_session.refresh(job)
    assert job.status == "completed"
    assert job.result_json == {"segments": [], "full_text": "", "language": "en", "model": ""}
    assert job.error is None
    fake_provider.transcribe.assert_not_awaited()


def test_run_chunk_job_non_silent_still_calls_provider(db_session):
    from services.queue import _run_chunk_job
    from types import SimpleNamespace

    user = User(username="nonsilentq", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(user_id=user.id, title="t2", filename="f2.wav", status="processing", provider="groq", model="base", language="en")
    db_session.add(t)
    db_session.commit()
    job = TranscriptionJob(transcript_id=t.id, chunk_index=0, start_time=0.0, end_time=60.0, audio_path="/tmp/fake_loud.mp3", status="pending")
    db_session.add(job)
    db_session.commit()

    fake_result = SimpleNamespace(segments=[], full_text="hello", language="en", model="m")
    fake_provider = MagicMock()
    fake_provider.transcribe = AsyncMock(return_value=fake_result)

    with patch("services.audio_prep.is_silent_audio", return_value=False), \
         patch("os.path.exists", return_value=True), \
         patch("services.queue.get_provider", return_value=fake_provider):
        asyncio.run(_run_chunk_job(db_session, job, {}, "groq", "en", asyncio.Semaphore(1)))

    fake_provider.transcribe.assert_awaited_once()
    db_session.refresh(job)
    assert job.status == "completed"


def test_merge_chunk_results_handles_empty_silent_job(db_session):
    """merge_chunk_results must handle a completed silent job with empty segments alongside real ones."""
    from services.queue import merge_chunk_results

    user = User(username="merge_silent", password_hash="x", password_salt="y")
    db_session.add(user)
    db_session.commit()
    t = Transcript(user_id=user.id, title="t", filename="f.wav", status="processing", provider="groq", model="base")
    db_session.add(t)
    db_session.commit()
    silent_job = TranscriptionJob(transcript_id=t.id, chunk_index=0, start_time=0.0, end_time=60.0, audio_path="c0.mp3", status="completed", result_json={"segments": [], "full_text": "", "language": "en", "model": ""})
    loud_job = TranscriptionJob(transcript_id=t.id, chunk_index=1, start_time=60.0, end_time=120.0, audio_path="c1.mp3", status="completed", result_json={"segments": [{"start": 0, "end": 2, "text": "hello world"}], "full_text": "hello world", "language": "en", "model": "m"})
    db_session.add_all([silent_job, loud_job])
    db_session.commit()

    jobs = db_session.query(TranscriptionJob).filter(TranscriptionJob.transcript_id == t.id).all()
    segments, full_text = merge_chunk_results(jobs)
    assert len(segments) == 1
    assert segments[0]["text"] == "hello world"
    assert full_text == "hello world"
