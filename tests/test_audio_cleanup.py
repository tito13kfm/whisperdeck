import os
import pytest
from unittest.mock import patch, MagicMock
from services.audio_cleanup import (
    cleanup_audio,
    filter_hallucinations,
    _find_longest_repeat,
    cleanup_demucs,
    CleanupResult,
    CleanupError,
)


# --- _find_longest_repeat ---

def test_find_longest_repeat_no_repetition():
    ng = [("hello", "world"), ("foo", "bar"), ("baz", "qux")]
    assert _find_longest_repeat(ng) is None

def test_find_longest_repeat_empty():
    assert _find_longest_repeat([]) is None

def test_find_longest_repeat_single():
    assert _find_longest_repeat([("a",)]) is None

def test_find_longest_repeat_exact():
    ng = [("a", "b"), ("a", "b"), ("a", "b")]
    assert _find_longest_repeat(ng) == 3

def test_find_longest_repeat_two_runs():
    ng = [("x",), ("x",), ("y",), ("z",), ("z",), ("z",)]
    assert _find_longest_repeat(ng) == 3

# Mutation: verify _find_longest_repeat fails on empty result
def test_find_longest_repeat_mutation_empty():
    """Mutation check: replacing body with 'return None' would fail this."""
    ng = [("a", "b"), ("a", "b")]
    assert _find_longest_repeat(ng) == 2


# --- filter_hallucinations ---

def test_filter_hallu_no_segments():
    assert filter_hallucinations([]) == []

def test_filter_hallu_no_repetition_keeps_all():
    segs = [
        {"text": "Hello world", "confidence": -0.5, "no_speech_prob": 0.1},
        {"text": "This is normal speech", "confidence": -1.0, "no_speech_prob": 0.2},
    ]
    result = filter_hallucinations(segs, rep_window=3)
    assert len(result) == 2
    assert result[0]["text"] == "Hello world"

def test_filter_hallu_high_logprob_keeps_all():
    segs = [
        {"text": "okay okay okay okay okay", "confidence": -0.3, "no_speech_prob": 0.1},
    ]
    result = filter_hallucinations(segs, rep_window=3, logprob_cutoff=-2.0)
    assert len(result) == 1  # confidence (-0.3) > cutoff (-2.0) → kept

def test_filter_hallu_low_logprob_repetition_removed():
    """thank thank thank thank thank = 5 tokens, rep_window=2 gives
    4 consecutive identical bigrams (thank,thank)"""
    segs = [
        {"text": "thank thank thank thank thank", "confidence": -3.5, "no_speech_prob": 0.1},
    ]
    result = filter_hallucinations(segs, rep_window=2, logprob_cutoff=-2.0, no_speech_cutoff=0.6)
    assert len(result) == 0

def test_filter_hallu_high_nsp_removed():
    segs = [
        {"text": "the the the the", "confidence": -0.5, "no_speech_prob": 0.8},
    ]
    result = filter_hallucinations(segs, rep_window=2, no_speech_cutoff=0.6)
    assert len(result) == 0

def test_filter_hallu_legitimate_repetition_kept():
    """Legitimate emphasis like 'okay okay okay' with high confidence."""
    segs = [
        {"text": "okay okay okay let us begin", "confidence": -0.5, "no_speech_prob": 0.1},
    ]
    result = filter_hallucinations(segs, rep_window=3, logprob_cutoff=-2.0, no_speech_cutoff=0.6)
    assert len(result) == 1

def test_filter_hallu_rep_window_1_skips():
    segs = [
        {"text": "the the the", "confidence": -3.5, "no_speech_prob": 0.1},
    ]
    result = filter_hallucinations(segs, rep_window=1)
    assert len(result) == 1

def test_filter_hallu_no_confidence_field_kept():
    segs = [
        {"text": "repeated repeated repeated", "no_speech_prob": 0.1},
    ]
    result = filter_hallucinations(segs, rep_window=3, logprob_cutoff=-2.0)
    assert len(result) == 1

# Mutation: verify filter_hallucinations removes hallucinations
def test_filter_hallu_mutation_removes():
    """Mutation check: replacing body with 'return segments' would leave hallu in."""
    segs = [
        {"text": "x x x x x x", "confidence": -4.0, "no_speech_prob": 0.1},
    ]
    result = filter_hallucinations(segs, rep_window=3, logprob_cutoff=-2.0)
    assert len(result) == 0


# --- cleanup_audio ---

@pytest.fixture
def default_settings():
    return {"cleanup_loudnorm_enabled": False, "cleanup_highpass_enabled": False,
            "cleanup_denoise_enabled": False}

def test_cleanup_no_steps_enabled_skips_all(default_settings, tmp_path):
    result = cleanup_audio_sync("fake.mp3", str(tmp_path), default_settings)
    assert result.audio_path == "fake.mp3"
    assert "loudnorm" in result.skipped_steps
    assert "denoise" in result.skipped_steps
    assert result.applied_steps == []
    assert result.failed_steps == []

def test_cleanup_ffmpeg_unavailable_skips(default_settings, tmp_path, monkeypatch):
    default_settings["cleanup_loudnorm_enabled"] = True
    monkeypatch.delenv("FFMPEG_DIR", raising=False)
    with patch("services.audio_cleanup._ffmpeg_available", return_value=False):
        result = cleanup_audio_sync("fake.mp3", str(tmp_path), default_settings)
    assert result.audio_path == "fake.mp3"
    assert "loudnorm" in result.skipped_steps
    assert "ffmpeg not available" in result.warnings[0]

def test_cleanup_loudnorm_applied(default_settings, tmp_path, monkeypatch):
    default_settings["cleanup_loudnorm_enabled"] = True
    monkeypatch.delenv("FFMPEG_DIR", raising=False)
    with patch("services.audio_cleanup._ffmpeg_available", return_value=True), \
         patch("services.audio_cleanup._ffmpeg_bin", return_value="ffmpeg"), \
         patch("subprocess.run") as mock_run, \
         patch("os.path.isfile", return_value=True):
        mock_run.return_value.returncode = 0
        result = cleanup_audio_sync("/tmp/test.mp3", str(tmp_path), default_settings)
    assert "loudnorm" in result.applied_steps
    assert result.audio_path.endswith("_clean.mp3")
    # Verify ffmpeg was called with loudnorm filter
    call_args = mock_run.call_args[0][0]
    af_arg_idx = call_args.index("-af") if "-af" in call_args else None
    assert af_arg_idx is not None
    assert "loudnorm=" in call_args[af_arg_idx + 1]

def test_cleanup_loudnorm_and_denoise_applied(default_settings, tmp_path, monkeypatch):
    default_settings["cleanup_loudnorm_enabled"] = True
    default_settings["cleanup_denoise_enabled"] = True
    monkeypatch.delenv("FFMPEG_DIR", raising=False)
    with patch("services.audio_cleanup._ffmpeg_available", return_value=True), \
         patch("services.audio_cleanup._ffmpeg_bin", return_value="ffmpeg"), \
         patch("subprocess.run") as mock_run, \
         patch("os.path.isfile", return_value=True):
        mock_run.return_value.returncode = 0
        result = cleanup_audio_sync("/tmp/test.mp3", str(tmp_path), default_settings)
    assert "loudnorm" in result.applied_steps
    assert "denoise" in result.applied_steps
    call_args = mock_run.call_args[0][0]
    af_arg_idx = call_args.index("-af") if "-af" in call_args else None
    assert af_arg_idx is not None
    assert "afftdn" in call_args[af_arg_idx + 1]

def test_cleanup_ffmpeg_failure_falls_back(default_settings, tmp_path, monkeypatch):
    default_settings["cleanup_loudnorm_enabled"] = True
    monkeypatch.delenv("FFMPEG_DIR", raising=False)
    with patch("services.audio_cleanup._ffmpeg_available", return_value=True), \
         patch("services.audio_cleanup._ffmpeg_bin", return_value="ffmpeg"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 1
        mock_run.return_value.stderr = "ffmpeg error: loudnorm filter failed"
        result = cleanup_audio_sync("/tmp/test.mp3", str(tmp_path), default_settings)
    assert result.audio_path == "/tmp/test.mp3"
    assert "loudnorm" in result.failed_steps
    assert result.warnings[0].startswith("ffmpeg cleanup failed")

# Mutation: verify cleanup_audio changes path when enabled — covered by
# test_cleanup_loudnorm_applied which asserts audio_path ends with _clean.mp3
# (would fail if function returned original path unconditionally).

# Helper: sync wrapper since cleanup_audio is async
def cleanup_audio_sync(audio_path, output_dir, user_settings):
    import asyncio
    return asyncio.run(cleanup_audio(audio_path, output_dir, user_settings))


# --- cleanup_demucs ---

def test_demucs_disabled_returns_original(tmp_path):
    result = cleanup_demucs_sync("fake.mp3", str(tmp_path), {"cleanup_demucs_enabled": False})
    assert result == "fake.mp3"

def test_demucs_import_error_returns_original(tmp_path):
    import builtins
    _real_import = builtins.__import__
    def _block_demucs(name, *args, **kwargs):
        if name == "demucs" or name.startswith("demucs."):
            raise ImportError("no demucs")
        return _real_import(name, *args, **kwargs)
    with patch("builtins.__import__", side_effect=_block_demucs):
        result = cleanup_demucs_sync("fake.mp3", str(tmp_path), {"cleanup_demucs_enabled": True})
    assert result == "fake.mp3"

def cleanup_demucs_sync(audio_path, output_dir, user_settings):
    import asyncio
    return asyncio.run(cleanup_demucs(audio_path, output_dir, user_settings))
