"""Channel-aware diarization primitives: energy VAD and bleed filtering."""
import numpy as np
import pytest

from services.diarization import DiarizationService

RATE = 16000


def _tone(seconds, amp=0.5):
    t = np.linspace(0, seconds, int(RATE * seconds), endpoint=False)
    return (amp * np.sin(2 * np.pi * 220 * t)).astype(np.float32)


def _silence(seconds):
    return np.zeros(int(RATE * seconds), dtype=np.float32)


def test_active_intervals_finds_speech_islands():
    channel = np.concatenate([_silence(1), _tone(2), _silence(2), _tone(1), _silence(1)])
    intervals = DiarizationService._active_intervals(channel, RATE)
    assert len(intervals) == 2
    s0, e0 = intervals[0]
    assert s0 == pytest.approx(1.0, abs=0.1)
    assert e0 == pytest.approx(3.0, abs=0.1)


def test_active_intervals_all_silence_is_empty():
    assert DiarizationService._active_intervals(_silence(3), RATE) == []


def test_active_intervals_merges_short_gaps():
    channel = np.concatenate([_tone(1), _silence(0.3), _tone(1)])
    intervals = DiarizationService._active_intervals(channel, RATE)
    assert len(intervals) == 1


def test_drop_bleed_removes_mic_intervals_dominated_by_system():
    mic = np.concatenate([_tone(1, amp=0.5), _tone(1, amp=0.05)])
    system = np.concatenate([_silence(1), _tone(1, amp=0.5)])
    intervals = [(0.0, 1.0), (1.0, 2.0)]
    kept = DiarizationService._drop_bleed(intervals, mic, system, RATE)
    assert kept == [(0.0, 1.0)]


def _stereo_flac(tmp_path, mic, system):
    import soundfile as sf
    path = tmp_path / "cap_16k_stereo.flac"
    sf.write(str(path), np.stack([mic, system], axis=1), RATE)
    return str(path)


@pytest.mark.asyncio
async def test_live_stereo_mic_becomes_you_and_system_goes_to_pyannote(monkeypatch, tmp_path):
    from services.diarization import DiarizationSegment
    svc = DiarizationService()
    # This test's scenario assumes pyannote is installed on the machine (it
    # mocks out only the heavy _run_pyannote_sync call, not availability) —
    # set this explicitly since the dev/CI venv running these tests may not
    # actually have pyannote installed.
    monkeypatch.setattr(svc, "pyannote_available", True)
    mic = np.concatenate([_tone(2), _silence(3)])
    system = np.concatenate([_silence(2), _tone(3)])
    path = _stereo_flac(tmp_path, mic, system)

    calls = {}

    def fake_sync(waveform, sample_rate, num_speakers, hf_token):
        calls["num_speakers"] = num_speakers
        calls["channels"] = waveform.shape[0]
        # Content check, not just shape: confirms it's actually the system
        # channel (index 1) that got routed to pyannote, not the mic channel
        # (index 0) — a channel-assignment swap would still pass a shape-only
        # check but fails this. Small tolerance for PCM_16 quantization on
        # FLAC write/read (soundfile's default FLAC subtype), not exact bit-for-bit.
        assert np.allclose(waveform[0], system, atol=1e-3)
        return [DiarizationSegment(start=2.0, end=5.0, speaker="SPEAKER_00")]

    monkeypatch.setattr(svc, "_run_pyannote_sync", fake_sync)
    result = await svc.diarize_live_stereo(path, num_speakers=3, hf_token=None)

    assert result.method == "live_stereo"
    assert calls["num_speakers"] == 2  # one fewer: the mic channel accounts for the local user
    assert calls["channels"] == 1  # system channel only, never the stereo pair
    speakers = {s.speaker for s in result.segments}
    assert "You" in speakers and "SPEAKER_00" in speakers


@pytest.mark.asyncio
async def test_live_stereo_silent_system_skips_pyannote(monkeypatch, tmp_path):
    svc = DiarizationService()
    # Bursty mic (tone / short gap / tone), not one unbroken tone: _active_intervals
    # is a relative-floor VAD (see its docstring's noted blind spot) and cannot
    # detect speech in a channel with zero internal silence — a real mic never
    # produces that anyway, so a continuous tone here would be an unrealistic
    # fixture, not a real edge case worth chasing in this helper.
    path = _stereo_flac(tmp_path, np.concatenate([_tone(1), _silence(0.3), _tone(1)]), _silence(2.3))

    def boom(*a, **k):
        raise AssertionError("pyannote must not run on a silent system channel")

    monkeypatch.setattr(svc, "_run_pyannote_sync", boom)
    result = await svc.diarize_live_stereo(path, num_speakers=2, hf_token=None)
    assert {s.speaker for s in result.segments} == {"You"}


@pytest.mark.asyncio
async def test_live_stereo_degrades_gracefully_without_pyannote(monkeypatch, tmp_path):
    """A system channel with activity would normally route to pyannote, but
    on a machine without pyannote installed this must degrade to mic-only
    "You" segments rather than raising a raw ImportError out of a worker
    thread — mirrors diarize_pyannote's own pyannote_available guard."""
    svc = DiarizationService()
    monkeypatch.setattr(svc, "pyannote_available", False)
    mic = np.concatenate([_tone(2), _silence(3)])
    system = np.concatenate([_silence(2), _tone(3)])
    path = _stereo_flac(tmp_path, mic, system)

    def boom(*a, **k):
        raise AssertionError("pyannote must not run when unavailable")

    monkeypatch.setattr(svc, "_run_pyannote_sync", boom)
    result = await svc.diarize_live_stereo(path, num_speakers=3, hf_token=None)
    assert {s.speaker for s in result.segments} == {"You"}


@pytest.mark.asyncio
async def test_live_stereo_rejects_mono_file(tmp_path):
    import soundfile as sf
    path = tmp_path / "mono.flac"
    sf.write(str(path), _tone(1), RATE)
    svc = DiarizationService()
    with pytest.raises(ValueError):
        await svc.diarize_live_stereo(str(path), num_speakers=2, hf_token=None)


@pytest.mark.asyncio
async def test_live_stereo_rejects_dual_mono(tmp_path):
    """A mono capture upmixed to 2 identical channels (e.g. MediaRecorder
    encoding mono opus despite the frontend's 2-channel merger graph, then
    ffmpeg's forced -ac 2) must raise rather than proceed: the bleed
    filter's mic-dominance test can never pass on identical channels, so
    the path would silently emit zero "You" segments while still shrinking
    pyannote's expected speaker count. Raising lets diarize_and_merge fall
    back to ordinary mixed-audio diarization."""
    channel = np.concatenate([_tone(1), _silence(1)])
    path = _stereo_flac(tmp_path, channel, channel)
    svc = DiarizationService()
    with pytest.raises(ValueError, match="identical"):
        await svc.diarize_live_stereo(path, num_speakers=2, hf_token=None)
