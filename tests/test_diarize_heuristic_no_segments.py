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


@pytest.mark.asyncio
async def test_diarize_heuristic_distributes_unused_labels():
    """When all gaps are < 1.5s, the gap-based alternation never advances
    past Speaker 1. The round-robin distribution pass must assign the
    remaining labels so speaker_count >= num_speakers."""
    segments = [
        {"text": "a", "start": 0.0, "end": 1.0},
        {"text": "b", "start": 1.2, "end": 2.0},
        {"text": "c", "start": 2.1, "end": 3.0},
        {"text": "d", "start": 3.0, "end": 4.0},
        {"text": "e", "start": 4.1, "end": 5.0},
        {"text": "f", "start": 5.0, "end": 6.0},
        {"text": "g", "start": 6.2, "end": 7.0},
        {"text": "h", "start": 7.1, "end": 8.0},
    ]

    svc = DiarizationService()
    result = await svc.diarize_heuristic("", num_speakers=4, segments=segments)

    speakers = {s.speaker for s in result.segments}
    assert len(speakers) == 4, f"expected 4 speakers, got {len(speakers)}: {speakers}"
    assert result.speaker_count == 4
    assert result.method == "heuristic"


@pytest.mark.asyncio
async def test_diarize_heuristic_preserves_existing_labels_in_mixed_gaps():
    """When gaps produce Speaker 1 and Speaker 2, the distribution pass
    must not overwrite Speaker 1 -- duplicate segments get unused labels
    while each existing label keeps at least one segment."""
    segments = [
        {"text": "a", "start": 0.0, "end": 1.0},
        {"text": "b", "start": 1.2, "end": 2.0},
        {"text": "c", "start": 2.1, "end": 3.0},
        {"text": "d", "start": 3.0, "end": 4.0},
        # gap > 1.5s advances to Speaker 2
        {"text": "e", "start": 5.6, "end": 6.0},
        {"text": "f", "start": 6.2, "end": 7.0},
        {"text": "g", "start": 7.1, "end": 8.0},
        {"text": "h", "start": 8.0, "end": 9.0},
    ]

    svc = DiarizationService()
    result = await svc.diarize_heuristic("", num_speakers=4, segments=segments)

    speakers = {s.speaker for s in result.segments}
    assert speakers == {"Speaker 1", "Speaker 2", "Speaker 3", "Speaker 4"}, \
        f"expected all 4 speakers, got {speakers}"
    assert result.speaker_count == 4
    assert result.method == "heuristic"
