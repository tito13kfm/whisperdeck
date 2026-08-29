import asyncio
import os
from unittest.mock import patch

import pytest

from services.audio_prep import extract_segment_clips


def test_extract_segment_clips_returns_none_for_invalid_ranges():
    async def run():
        with patch("services.audio_prep.ffmpeg_available", return_value=True), \
             patch("services.audio_prep.get_audio_duration", return_value=10.0), \
             patch("services.audio_prep.subprocess.run") as mock_run:
            mock_run.return_value.returncode = 0
            clips = [
                {"start": 0.0, "end": 1.0},
                {"start": 5.0, "end": 3.0},
                {"start": 20.0, "end": 25.0},
            ]
            out = await extract_segment_clips("a.mp3", clips, "/tmp", batch_size=10)
            assert out[0] is not None
            assert out[1] is None
            assert out[2] is None
            assert mock_run.call_count == 1

    asyncio.run(run())


def test_extract_segment_clips_batches_and_handles_ffmpeg_failure():
    async def run():
        def fake_run(args, capture_output=True, text=True):
            class R:
                pass
            r = R()
            start_arg = args[args.index("-ss") + 1]
            r.returncode = 1 if float(start_arg) == 2.0 else 0
            return r

        with patch("services.audio_prep.ffmpeg_available", return_value=True), \
             patch("services.audio_prep.get_audio_duration", return_value=10.0), \
             patch("services.audio_prep.subprocess.run", side_effect=fake_run):
            clips = [
                {"start": 0.0, "end": 1.0},
                {"start": 2.0, "end": 3.0},
                {"start": 4.0, "end": 5.0},
            ]
            out = await extract_segment_clips("a.mp3", clips, "/tmp", batch_size=2)
            assert out[0] is not None
            assert out[1] is None
            assert out[2] is not None

    asyncio.run(run())


def test_extract_segment_clips_empty_returns_empty():
    async def run():
        with patch("services.audio_prep.ffmpeg_available", return_value=True):
            out = await extract_segment_clips("a.mp3", [], "/tmp")
            assert out == []

    asyncio.run(run())
