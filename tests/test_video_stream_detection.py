"""has_video_stream(): ffprobe-based check for a video stream, independent
of file extension — a .mp4 that's actually audio-only (or a misnamed
file) must not falsely report having video."""
import shutil
import subprocess

import pytest

from services.audio_prep import has_video_stream

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg not on PATH")


def _make_video(path):
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "testsrc=duration=1:size=64x64:rate=5",
         "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-shortest",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", str(path)],
        check=True, capture_output=True,
    )


def _make_audio_only(path):
    # -strict -1: newer ffmpeg builds refuse to mux mp3 at 8kHz into an mp4
    # container ("not standard") without this, which matters for the
    # misnamed-.mp4 case below even though the plain .mp3 case doesn't
    # need it.
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "1",
         "-c:a", "libmp3lame", "-strict", "-1", str(path)],
        check=True, capture_output=True,
    )


def test_detects_video_stream(tmp_path):
    video = tmp_path / "clip.mp4"
    _make_video(video)
    assert has_video_stream(str(video)) is True


def test_audio_only_file_has_no_video_stream(tmp_path):
    audio = tmp_path / "clip.mp3"
    _make_audio_only(audio)
    assert has_video_stream(str(audio)) is False


def test_misnamed_audio_only_mp4_has_no_video_stream(tmp_path):
    """Extension lies — an .mp4 with only an audio stream must still
    report False, since this drives whether we retain/serve it as video."""
    audio = tmp_path / "not_really_video.mp4"
    _make_audio_only(audio)
    assert has_video_stream(str(audio)) is False


def test_missing_file_returns_false_not_raise(tmp_path):
    assert has_video_stream(str(tmp_path / "nope.mp4")) is False
