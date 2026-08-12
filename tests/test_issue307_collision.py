"""Regression test for issue #307: same-basename collision."""

import os
from unittest.mock import patch
import asyncio


def _run(coro):
    return asyncio.run(coro)


def test_cleanup_same_basename_produces_distinct_paths(tmp_path):
    """Two cleanup_audio calls with same basename must not collide."""
    from services.audio_cleanup import cleanup_audio

    settings = {
        "cleanup_loudnorm_enabled": True,
        "cleanup_highpass_enabled": False,
        "cleanup_denoise_enabled": False,
    }
    # Mock ffmpeg to avoid actual transcoding
    with patch("services.audio_cleanup._ffmpeg_available", return_value=True), \
         patch("services.audio_cleanup._ffmpeg_bin", return_value="ffmpeg"), \
         patch("subprocess.run") as mock_run, \
         patch("os.path.isfile", return_value=True):
        mock_run.return_value.returncode = 0
        r1 = _run(cleanup_audio("/tmp/recording.m4a", str(tmp_path), settings))
        r2 = _run(cleanup_audio("/tmp/recording.m4a", str(tmp_path), settings))
    assert r1.audio_path != r2.audio_path, "same basename must produce distinct cleaned paths"
    assert r1.audio_path.endswith("_clean.mp3")
    assert r2.audio_path.endswith("_clean.mp3")
    # Both should contain the base before suffix
    assert "recording" in os.path.basename(r1.audio_path)
    assert "recording" in os.path.basename(r2.audio_path)


def test_transcode_same_basename_produces_distinct_paths(tmp_path):
    """Two transcode_for_upload calls with same basename must not collide."""
    from services.audio_prep import transcode_for_upload

    with patch("services.audio_prep.ffmpeg_available", return_value=True), \
         patch("services.audio_prep._ffmpeg_bin", return_value="ffmpeg"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        p1 = _run(transcode_for_upload("/tmp/recording.m4a", str(tmp_path)))
        p2 = _run(transcode_for_upload("/tmp/recording.m4a", str(tmp_path)))
    assert p1 != p2
    assert p1.endswith("_16k.mp3")
    assert p2.endswith("_16k.mp3")


def test_transcode_stereo_same_basename_distinct(tmp_path):
    from services.audio_prep import transcode_stereo_for_diarization

    with patch("services.audio_prep.ffmpeg_available", return_value=True), \
         patch("services.audio_prep._ffmpeg_bin", return_value="ffmpeg"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        p1 = _run(transcode_stereo_for_diarization("/tmp/recording.m4a", str(tmp_path)))
        p2 = _run(transcode_stereo_for_diarization("/tmp/recording.m4a", str(tmp_path)))
    assert p1 != p2
    assert p1.endswith("_16k_stereo.flac")


def test_chunk_same_basename_distinct_prefix(tmp_path):
    """chunk_audio with same basename input should use distinct suffix per invocation."""
    from services.audio_prep import chunk_audio

    # Create a dummy audio file to satisfy getsize; chunk_audio will call get_audio_duration and detect_silence_midpoints
    dummy = tmp_path / "recording.m4a"
    dummy.write_bytes(b"x" * 10000)

    with patch("services.audio_prep.ffmpeg_available", return_value=True), \
         patch("services.audio_prep.get_audio_duration", return_value=60.0), \
         patch("services.audio_prep.detect_silence_midpoints", return_value=[]), \
         patch("services.audio_prep.is_silent_audio", return_value=False), \
         patch("services.audio_prep._ffmpeg_bin", return_value="ffmpeg"), \
         patch("subprocess.run") as mock_run, \
         patch("os.path.getsize", return_value=10000):
        mock_run.return_value.returncode = 0
        c1 = _run(chunk_audio(str(dummy), str(tmp_path), target_chunk_bytes=5000))
        c2 = _run(chunk_audio(str(dummy), str(tmp_path), target_chunk_bytes=5000))
    # Each call produces chunks with same count but different path prefix
    assert c1[0]["path"] != c2[0]["path"]
    # Within same invocation, chunks share same suffix but different index
    if len(c1) > 1:
        base1 = os.path.basename(c1[0]["path"])
        base2 = os.path.basename(c1[1]["path"])
        # suffix part before _chunk should match
        prefix1 = base1.split("_chunk")[0]
        prefix2 = base2.split("_chunk")[0]
        assert prefix1 == prefix2, "chunks from same invocation share suffix"
        # but different invocations differ
        other_prefix = os.path.basename(c2[0]["path"]).split("_chunk")[0]
        assert prefix1 != other_prefix


def test_demucs_same_basename_distinct_dir(tmp_path):
    """cleanup_demucs should produce distinct out_dir per call."""
    from services import audio_cleanup

    settings = {"cleanup_demucs_enabled": True}
    # Block demucs import, instead patch the whole function to capture out_dir
    # We test suffix generation by mocking demucs.separate.main and checking the path
    import types, sys
    fake_demucs = types.ModuleType("demucs")
    fake_sep = types.ModuleType("demucs.separate")
    captured = []

    def fake_main(args):
        # args: ["--two-stems","vocals","-o", out_dir, audio_path]
        out_dir = args[args.index("-o") + 1]
        captured.append(out_dir)
        # create expected vocals file so function returns successfully
        import os as _os
        base = _os.path.splitext(_os.path.basename(args[-1]))[0]
        vocals_dir = _os.path.join(out_dir, "htdemucs", base)
        _os.makedirs(vocals_dir, exist_ok=True)
        open(_os.path.join(vocals_dir, "vocals.wav"), "w").close()

    fake_sep.main = fake_main
    fake_demucs.separate = fake_sep
    sys.modules["demucs"] = fake_demucs
    sys.modules["demucs.separate"] = fake_sep
    try:
        p1 = _run(audio_cleanup.cleanup_demucs("/tmp/recording.m4a", str(tmp_path), settings))
        out1 = captured[-1]
        p2 = _run(audio_cleanup.cleanup_demucs("/tmp/recording.m4a", str(tmp_path), settings))
        out2 = captured[-1]
        assert out1 != out2
        assert "_demucs" in out1
        assert "_demucs" in out2
    finally:
        sys.modules.pop("demucs", None)
        sys.modules.pop("demucs.separate", None)


def test_extract_clips_same_basename_distinct(tmp_path):
    """extract_clips_concat same basename should produce distinct out paths."""
    from services.audio_prep import extract_clips_concat

    dummy = tmp_path / "recording.m4a"
    dummy.write_bytes(b"x" * 1000)

    with patch("services.audio_prep.ffmpeg_available", return_value=True), \
         patch("services.audio_prep.get_audio_duration", return_value=60.0), \
         patch("services.audio_prep._ffmpeg_bin", return_value="ffmpeg"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value.returncode = 0
        # create clips that will exercise the suffix
        clips = [{"start": 0, "end": 5}, {"start": 10, "end": 15}]
        # Patch to capture out_path via side effect: we need to let _run succeed
        # The function returns out_path; just check distinctness
        p1 = _run(extract_clips_concat(str(dummy), clips, str(tmp_path)))
        p2 = _run(extract_clips_concat(str(dummy), clips, str(tmp_path)))
    assert p1 != p2
    assert "_seed.wav" in p1 or "_seed" in p1



