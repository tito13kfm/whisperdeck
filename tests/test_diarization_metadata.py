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
