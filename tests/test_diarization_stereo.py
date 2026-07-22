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
