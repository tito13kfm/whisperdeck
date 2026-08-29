import asyncio
import os
from unittest.mock import patch

import pytest

from services.audio_prep import extract_segment_clips


def test_extract_segment_clips_returns_none_for_invalid_ranges():
    async def run():
        with patch("services.audio_prep.ffmpeg_available", return_value=True), \
             patch("services.audio_prep.get_audio_duration", return_value=10.0), \
             patch("services.audio_prep.subprocess.run") as mock_run, \
             patch("services.audio_prep.os.path.exists", return_value=True), \
             patch("services.audio_prep.os.path.getsize", return_value=1000):
            clips = [
                {"start": 0.0, "end": 1.0},
                {"start": 5.0, "end": 3.0},
                {"start": 20.0, "end": 25.0},
            ]
            out = await extract_segment_clips("a.mp3", clips, "/tmp", batch_size=10)
            assert out[0] is not None
            assert out[1] is None
            assert out[2] is None
            # one valid clip in one batch -> exactly one ffmpeg invocation
            assert mock_run.call_count == 1

    asyncio.run(run())


def test_extract_segment_clips_batches_into_one_ffmpeg_call_per_batch():
    """The whole point of batching: N valid clips in one batch must cost
    one ffmpeg process, not N. This is the regression the original
    per-clip-subprocess implementation would fail."""

    async def run():
        with patch("services.audio_prep.ffmpeg_available", return_value=True), \
             patch("services.audio_prep.get_audio_duration", return_value=1000.0), \
             patch("services.audio_prep.subprocess.run") as mock_run, \
             patch("services.audio_prep.os.path.exists", return_value=True), \
             patch("services.audio_prep.os.path.getsize", return_value=1000):
            clips = [{"start": float(i), "end": float(i) + 1.0} for i in range(40)]
            out = await extract_segment_clips("a.mp3", clips, "/tmp", batch_size=20)
            assert len(out) == 40
            assert all(p is not None for p in out)
            # 40 clips / batch_size 20 -> 2 ffmpeg invocations, not 40
            assert mock_run.call_count == 2
            # each invocation must carry every clip in its batch as its own
            # -ss/-to output group, not one clip per process
            first_call_args = mock_run.call_args_list[0].args[0]
            assert first_call_args.count("-ss") == 20

    asyncio.run(run())


def test_extract_segment_clips_handles_partial_batch_failure():
    """One output failing inside a batch must not take down the rest of
    that batch's already-written outputs."""

    async def run():
        def fake_exists(path):
            return "seg1." not in path

        with patch("services.audio_prep.ffmpeg_available", return_value=True), \
             patch("services.audio_prep.get_audio_duration", return_value=10.0), \
             patch("services.audio_prep.subprocess.run") as mock_run, \
             patch("services.audio_prep.os.path.exists", side_effect=fake_exists), \
             patch("services.audio_prep.os.path.getsize", return_value=1000):
            clips = [
                {"start": 0.0, "end": 1.0},
                {"start": 2.0, "end": 3.0},
                {"start": 4.0, "end": 5.0},
            ]
            out = await extract_segment_clips("a.mp3", clips, "/tmp", batch_size=10)
            assert out[0] is not None
            assert out[1] is None
            assert out[2] is not None
            assert mock_run.call_count == 1

    asyncio.run(run())


def test_extract_segment_clips_survives_subprocess_oserror(tmp_path):
    """A batch whose ffmpeg process can't even launch must fail only that
    batch, not raise up and wipe out other batches' results."""

    async def run():
        def fake_run(args, capture_output=True, text=True):
            start_arg = args[args.index("-ss") + 1]
            if float(start_arg) == 0.0:
                raise OSError("no such file or directory")
            out_path = args[-1]
            with open(out_path, "wb") as f:
                f.write(b"0" * 100)
            class R:
                returncode = 0
            return R()

        with patch("services.audio_prep.ffmpeg_available", return_value=True), \
             patch("services.audio_prep.get_audio_duration", return_value=10.0), \
             patch("services.audio_prep.subprocess.run", side_effect=fake_run):
            clips = [
                {"start": 0.0, "end": 1.0},
                {"start": 5.0, "end": 6.0},
            ]
            out = await extract_segment_clips("a.mp3", clips, str(tmp_path), batch_size=1)
            assert out[0] is None
            assert out[1] is not None
            assert os.path.exists(out[1])

    asyncio.run(run())


def test_extract_segment_clips_empty_returns_empty():
    async def run():
        with patch("services.audio_prep.ffmpeg_available", return_value=True):
            out = await extract_segment_clips("a.mp3", [], "/tmp")
            assert out == []

    asyncio.run(run())
