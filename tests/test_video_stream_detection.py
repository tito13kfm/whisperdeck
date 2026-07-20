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


def _make_audio_with_cover_art(path):
    """An audio file with an embedded cover-art image, as produced by
    podcast exports, voice-memo apps, and many music files — ffprobe
    reports the cover art as a video stream (attached_pic disposition),
    which must not count as "has video" for playback-UI purposes."""
    base = path.parent / f"{path.stem}_base.mp3"
    cover = path.parent / f"{path.stem}_cover.jpg"
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "anullsrc=r=8000:cl=mono", "-t", "1",
         "-c:a", "libmp3lame", str(base)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-f", "lavfi", "-i", "color=c=blue:s=64x64:d=1",
         "-frames:v", "1", "-update", "1", str(cover)],
        check=True, capture_output=True,
    )
    subprocess.run(
        ["ffmpeg", "-y", "-i", str(base), "-i", str(cover),
         "-map", "0:a", "-map", "1:v", "-c:a", "copy", "-c:v", "mjpeg",
         "-disposition:v", "attached_pic", str(path)],
        check=True, capture_output=True,
    )


def test_embedded_cover_art_is_not_a_video_stream(tmp_path):
    """Cover art must not be mistaken for a real video stream — otherwise
    a plain audio file with album art would trigger the video-playback UI
    for what's actually just a static thumbnail."""
    audio = tmp_path / "podcast_with_art.mp3"
    _make_audio_with_cover_art(audio)
    assert has_video_stream(str(audio)) is False


def test_missing_file_returns_false_not_raise(tmp_path):
    assert has_video_stream(str(tmp_path / "nope.mp4")) is False
