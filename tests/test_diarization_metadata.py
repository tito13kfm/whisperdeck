"""diarize_and_merge returns and callers persist the diarization method."""
import pytest

from services.diarization import DiarizationService


@pytest.mark.asyncio
async def test_diarize_and_merge_returns_method_heuristic(monkeypatch, tmp_path):
    svc = DiarizationService()
    monkeypatch.setattr(svc, "_check_pyannote", lambda: False)
    segments = [
        {"start": 0.0, "end": 2.0, "text": "hello"},
        {"start": 4.0, "end": 6.0, "text": "world"},  # 2s gap flips the heuristic speaker
    ]
    merged, count, method = await svc.diarize_and_merge(
        str(tmp_path / "missing.mp3"), num_speakers=2, segments=segments,
    )
    assert method == "heuristic"
    assert count >= 1
    assert all("speaker" in s for s in merged)


@pytest.mark.asyncio
async def test_diarize_and_merge_falls_through_to_heuristic_without_pyannote(monkeypatch, tmp_path):
    """A stereo copy existing on disk doesn't matter if pyannote isn't
    installed — diarize_live_stereo needs pyannote for the remote channel,
    so the outer gate must route straight to the heuristic, never call it."""
    svc = DiarizationService()
    monkeypatch.setattr(svc, "_check_pyannote", lambda: False)

    async def fail_stereo(*a, **k):
        raise AssertionError("diarize_live_stereo must not run when pyannote is unavailable")

    monkeypatch.setattr(svc, "diarize_live_stereo", fail_stereo)
    stereo = tmp_path / "s.flac"
    stereo.write_bytes(b"x")  # existence check only; must never be read
    segments = [
        {"start": 0.0, "end": 2.0, "text": "hello"},
        {"start": 4.0, "end": 6.0, "text": "world"},
    ]
    merged, count, method = await svc.diarize_and_merge(
        str(tmp_path / "missing.mp3"), num_speakers=2, segments=segments,
        stereo_audio_path=str(stereo),
    )
    assert method == "heuristic"
    assert count >= 1


@pytest.mark.asyncio
async def test_diarize_and_merge_returns_method_pyannote(monkeypatch, tmp_path):
    from services.diarization import DiarizationResult, DiarizationSegment
    svc = DiarizationService()
    monkeypatch.setattr(svc, "_check_pyannote", lambda: True)

    async def fake_pyannote(audio_path, num_speakers=None, hf_token=None):
        return DiarizationResult(
            segments=[DiarizationSegment(start=0.0, end=6.0, speaker="SPEAKER_00")],
            speaker_count=1, method="pyannote",
        )

    monkeypatch.setattr(svc, "diarize_pyannote", fake_pyannote)
    merged, count, method = await svc.diarize_and_merge(
        str(tmp_path / "a.mp3"), num_speakers=1,
        segments=[{"start": 0.0, "end": 2.0, "text": "hi"}],
    )
    assert method == "pyannote"
    assert merged[0]["speaker"] == "SPEAKER_00"


@pytest.mark.asyncio
async def test_diarize_and_merge_prefers_live_stereo(monkeypatch, tmp_path):
    from services.diarization import DiarizationResult, DiarizationSegment
    svc = DiarizationService()
    monkeypatch.setattr(svc, "_check_pyannote", lambda: True)

    async def fake_stereo(stereo_path, num_speakers=None, hf_token=None):
        return DiarizationResult(
            segments=[DiarizationSegment(start=0.0, end=2.0, speaker="You")],
            speaker_count=1, method="live_stereo",
        )

    async def fail_pyannote(*a, **k):
        raise AssertionError("mixed-audio path must not run when a stereo copy exists")

    monkeypatch.setattr(svc, "diarize_live_stereo", fake_stereo)
    monkeypatch.setattr(svc, "diarize_pyannote", fail_pyannote)
    stereo = tmp_path / "s.flac"
    stereo.write_bytes(b"x")  # existence check only; the fake never reads it
    merged, count, method = await svc.diarize_and_merge(
        str(tmp_path / "a.mp3"), num_speakers=2,
        segments=[{"start": 0.0, "end": 1.0, "text": "hi"}],
        stereo_audio_path=str(stereo),
    )
    assert method == "live_stereo"
    assert merged[0]["speaker"] == "You"


@pytest.mark.asyncio
async def test_diarize_and_merge_falls_back_to_pyannote_on_stereo_failure(monkeypatch, tmp_path):
    from services.diarization import DiarizationResult, DiarizationSegment
    svc = DiarizationService()
    monkeypatch.setattr(svc, "_check_pyannote", lambda: True)

    async def broken_stereo(stereo_path, num_speakers=None, hf_token=None):
        raise RuntimeError("stereo channel split failed")

    async def fake_pyannote(audio_path, num_speakers=None, hf_token=None):
        return DiarizationResult(
            segments=[DiarizationSegment(start=0.0, end=6.0, speaker="SPEAKER_00")],
            speaker_count=1, method="pyannote",
        )

    monkeypatch.setattr(svc, "diarize_live_stereo", broken_stereo)
    monkeypatch.setattr(svc, "diarize_pyannote", fake_pyannote)
    stereo = tmp_path / "s.flac"
    stereo.write_bytes(b"x")  # existence check only; the fake never reads it
    merged, count, method = await svc.diarize_and_merge(
        str(tmp_path / "a.mp3"), num_speakers=2,
        segments=[{"start": 0.0, "end": 2.0, "text": "hi"}],
        stereo_audio_path=str(stereo),
    )
    assert method == "pyannote"
    assert merged[0]["speaker"] == "SPEAKER_00"


@pytest.mark.asyncio
async def test_diarize_and_merge_falls_through_when_stereo_file_missing(monkeypatch, tmp_path):
    from services.diarization import DiarizationResult, DiarizationSegment
    svc = DiarizationService()
    monkeypatch.setattr(svc, "_check_pyannote", lambda: True)

    async def fail_stereo(*a, **k):
        raise AssertionError("live-stereo path must not run when the stereo file doesn't exist")

    async def fake_pyannote(audio_path, num_speakers=None, hf_token=None):
        return DiarizationResult(
            segments=[DiarizationSegment(start=0.0, end=6.0, speaker="SPEAKER_00")],
            speaker_count=1, method="pyannote",
        )

    monkeypatch.setattr(svc, "diarize_live_stereo", fail_stereo)
    monkeypatch.setattr(svc, "diarize_pyannote", fake_pyannote)
    merged, count, method = await svc.diarize_and_merge(
        str(tmp_path / "a.mp3"), num_speakers=2,
        segments=[{"start": 0.0, "end": 2.0, "text": "hi"}],
        stereo_audio_path=str(tmp_path / "missing_stereo.flac"),
    )
    assert method == "pyannote"
    assert merged[0]["speaker"] == "SPEAKER_00"
