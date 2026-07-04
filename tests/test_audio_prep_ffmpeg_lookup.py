import os
import pytest
from services import audio_prep


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    monkeypatch.delenv("FFMPEG_DIR", raising=False)
    yield


def test_ffmpeg_bin_defaults_to_path_lookup():
    assert audio_prep._ffmpeg_bin() == "ffmpeg"


def test_ffprobe_bin_defaults_to_path_lookup():
    assert audio_prep._ffprobe_bin() == "ffprobe"


def test_ffmpeg_bin_uses_ffmpeg_dir_when_set(monkeypatch):
    monkeypatch.setenv("FFMPEG_DIR", r"C:\WhisperDeck\ffmpeg")
    assert audio_prep._ffmpeg_bin() == os.path.join(r"C:\WhisperDeck\ffmpeg", "ffmpeg.exe")


def test_ffprobe_bin_uses_ffmpeg_dir_when_set(monkeypatch):
    monkeypatch.setenv("FFMPEG_DIR", r"C:\WhisperDeck\ffmpeg")
    assert audio_prep._ffprobe_bin() == os.path.join(r"C:\WhisperDeck\ffmpeg", "ffprobe.exe")


def test_ffmpeg_available_checks_bundled_path_when_ffmpeg_dir_set(monkeypatch, tmp_path):
    bundled_dir = tmp_path / "ffmpeg"
    bundled_dir.mkdir()
    (bundled_dir / "ffmpeg.exe").write_bytes(b"")
    monkeypatch.setenv("FFMPEG_DIR", str(bundled_dir))
    assert audio_prep.ffmpeg_available() is True


def test_ffmpeg_available_false_when_ffmpeg_dir_set_but_binary_missing(monkeypatch, tmp_path):
    bundled_dir = tmp_path / "ffmpeg"
    bundled_dir.mkdir()
    monkeypatch.setenv("FFMPEG_DIR", str(bundled_dir))
    assert audio_prep.ffmpeg_available() is False
