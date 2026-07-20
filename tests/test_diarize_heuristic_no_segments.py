"""Regression test for the standalone POST /api/diarize endpoint's default
heuristic path: services/diarization.py's diarize_heuristic() used to
silently return an empty segment list whenever called without pre-existing
transcript segments (the "pseudo-segments from silence detection" logic was
never implemented past a comment). This covers the fix: energy-based voice
activity detection builds pseudo-segments from raw audio so the pause-gap
speaker heuristic has something to cluster."""
import numpy as np
import soundfile as sf
import pytest

from services.diarization import DiarizationService


def _write_two_burst_wav(path, sr=16000):
    """~0.5s tone, ~1s silence, ~0.5s tone — should split into 2 segments
    under the default 0.5s silence-gap threshold."""
    silence = np.zeros(int(sr * 1.0), dtype=np.float32)
    t = np.linspace(0, 0.5, int(sr * 0.5), endpoint=False)
    tone = (0.5 * np.sin(2 * np.pi * 220 * t)).astype(np.float32)
    audio = np.concatenate([tone, silence, tone])
    sf.write(str(path), audio, sr)


@pytest.mark.asyncio
async def test_diarize_heuristic_without_segments_returns_pseudo_segments(tmp_path):
    wav_path = tmp_path / "two_bursts.wav"
    _write_two_burst_wav(wav_path)

    svc = DiarizationService()
    result = await svc.diarize_heuristic(str(wav_path), num_speakers=2, segments=None)

    assert result.method == "heuristic"
    assert len(result.segments) >= 1
    assert result.speaker_count >= 1
    for seg in result.segments:
        assert seg.end > seg.start


@pytest.mark.asyncio
async def test_diarize_heuristic_silent_file_returns_empty(tmp_path):
    wav_path = tmp_path / "silence.wav"
    sf.write(str(wav_path), np.zeros(16000, dtype=np.float32), 16000)

    svc = DiarizationService()
    result = await svc.diarize_heuristic(str(wav_path), num_speakers=2, segments=None)

    assert result.segments == []
    assert result.speaker_count == 0
