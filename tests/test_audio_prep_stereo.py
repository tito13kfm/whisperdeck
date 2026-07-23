"""transcode_stereo_for_diarization produces a 16 kHz 2-channel FLAC."""
import asyncio
import os
import wave
import struct

import pytest

from services.audio_prep import ffmpeg_available, transcode_stereo_for_diarization

pytestmark = pytest.mark.skipif(not ffmpeg_available(), reason="ffmpeg not installed")


def _write_stereo_wav(path, seconds=1, rate=44100):
    with wave.open(str(path), "wb") as w:
        w.setnchannels(2)
        w.setsampwidth(2)
        w.setframerate(rate)
        frames = b"".join(
            struct.pack("<hh", 3000, -3000) for _ in range(rate * seconds)
        )
        w.writeframes(frames)


@pytest.mark.asyncio
async def test_stereo_transcode_keeps_two_channels(tmp_path):
    src = tmp_path / "cap.wav"
    _write_stereo_wav(src)
    out = await transcode_stereo_for_diarization(str(src), str(tmp_path))
    assert out.endswith("_16k_stereo.flac")
    import soundfile as sf
    data, rate = sf.read(out, always_2d=True)
    assert rate == 16000
    assert data.shape[1] == 2
