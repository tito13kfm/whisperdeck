"""Audio preprocessing — normalize uploads before sending to cloud providers.

Video files (mp4, mov, ...) and long recordings can exceed a provider's
upload size limit. Whisper-family models run at 16kHz mono internally
regardless of input format, so transcoding down to that loses nothing the
model would have used anyway, while shrinking the upload substantially and
stripping any video track.

For recordings that are still too large after transcoding, chunk_audio()
splits them at silence boundaries (found via ffmpeg's silencedetect filter)
so each piece fits under the provider's size cap.
"""
import asyncio
import os
import re
import shutil
import subprocess


class AudioPrepError(Exception):
    """Raised when ffmpeg is unavailable or the transcode fails."""
    pass


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


async def transcode_for_upload(input_path: str, output_dir: str, bitrate_kbps: int = 128) -> str:
    """Transcode to 16kHz mono MP3 and return the new file path.

    Streams through ffmpeg rather than decoding into memory, so multi-hour
    recordings don't spike RAM. Does not attempt to handle files that still
    exceed the provider's limit after transcoding — chunk_audio() handles
    that as a separate step.

    bitrate_kbps defaults to 128 (was 64) — sample rate (16kHz) is the real
    ceiling on what Whisper-family models use, since they resample to that
    internally regardless of input; bitrate only governs compression
    artifacts within that 16kHz signal, which matters more for noisy/
    accented audio than the file-size savings of a lower bitrate did once
    chunking removed size pressure as a reason to keep it low.
    """
    if not ffmpeg_available():
        raise AudioPrepError(
            "ffmpeg is not installed or not on PATH. It's required to prepare "
            "audio/video uploads for cloud transcription providers. "
            "See INSTALL.md."
        )

    base = os.path.splitext(os.path.basename(input_path))[0]
    output_path = os.path.join(output_dir, f"{base}_16k.mp3")

    cmd = [
        "ffmpeg", "-y",
        "-i", input_path,
        "-vn",
        "-ac", "1",
        "-ar", "16000",
        "-c:a", "libmp3lame",
        "-b:a", f"{bitrate_kbps}k",
        output_path,
    ]

    def _run():
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise AudioPrepError(f"ffmpeg transcode failed: {result.stderr[-2000:]}")
        return output_path

    return await asyncio.to_thread(_run)


def get_audio_duration(audio_path: str) -> float:
    """Return the audio file's duration in seconds via ffprobe."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", audio_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise AudioPrepError(f"ffprobe failed to read duration: {result.stderr[-500:]}")
    return float(result.stdout.strip())


_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")


def detect_silence_midpoints(audio_path: str, noise_db: str = "-30dB", min_duration: float = 0.5) -> list[float]:
    """Return timestamps (seconds) at the midpoint of each detected silence
    gap, in order. Cutting a chunk boundary at one of these midpoints keeps
    the cut roughly equidistant from speech on either side.

    A single-pass ffmpeg filter — no decode-to-file, no ML model, adds low
    single-digit seconds even on a multi-hour recording.
    """
    result = subprocess.run(
        [
            "ffmpeg", "-i", audio_path,
            "-af", f"silencedetect=noise={noise_db}:d={min_duration}",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    midpoints = []
    for match in _SILENCE_END_RE.finditer(result.stderr):
        silence_end = float(match.group(1))
        silence_duration = float(match.group(2))
        silence_start = silence_end - silence_duration
        midpoints.append((silence_start + silence_end) / 2)
    return midpoints


async def extract_clips_concat(
    audio_path: str,
    clips: list[dict],
    output_dir: str,
    max_total_seconds: float = 30.0,
) -> str:
    """Extract the given {start, end} time ranges and join them into one
    16kHz mono wav, returned as a path the caller must delete when done.

    Used to build a single voice-enrollment sample from transcript lines
    flagged as seeds. Every clip is re-encoded to the same wav format,
    which is what makes the concat demuxer's -c copy join safe. Total
    audio is capped at max_total_seconds — the embedding backends only
    look at the first ~30s anyway.
    """
    if not ffmpeg_available():
        raise AudioPrepError("ffmpeg is not installed or not on PATH. See INSTALL.md.")

    duration = get_audio_duration(audio_path)
    base = os.path.splitext(os.path.basename(audio_path))[0]

    selected = []
    total = 0.0
    for clip in clips:
        start = max(0.0, float(clip["start"]))
        end = min(duration, float(clip["end"]))
        if end <= start:
            continue
        if total >= max_total_seconds:
            break
        end = min(end, start + (max_total_seconds - total))
        selected.append((start, end))
        total += end - start
    if not selected:
        raise AudioPrepError("No usable clip ranges (empty or outside the audio)")

    def _run():
        part_paths = []
        list_path = os.path.join(output_dir, f"{base}_seed_list.txt")
        out_path = os.path.join(output_dir, f"{base}_seed.wav")
        try:
            for i, (start, end) in enumerate(selected):
                part = os.path.join(output_dir, f"{base}_seed_part{i}.wav")
                result = subprocess.run(
                    [
                        "ffmpeg", "-y", "-i", audio_path,
                        "-ss", str(start), "-to", str(end),
                        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                        part,
                    ],
                    capture_output=True, text=True,
                )
                if result.returncode != 0:
                    raise AudioPrepError(f"ffmpeg clip extract failed: {result.stderr[-2000:]}")
                part_paths.append(part)

            with open(list_path, "w", encoding="utf-8") as f:
                for p in part_paths:
                    # Forward slashes: the concat demuxer parses backslashes
                    # as escapes inside the quoted filename on Windows.
                    f.write(f"file '{os.path.abspath(p).replace(os.sep, '/')}'\n")
            result = subprocess.run(
                ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", out_path],
                capture_output=True, text=True,
            )
            if result.returncode != 0:
                raise AudioPrepError(f"ffmpeg clip concat failed: {result.stderr[-2000:]}")
            return out_path
        finally:
            for p in part_paths + [list_path]:
                try:
                    os.remove(p)
                except OSError:
                    pass

    return await asyncio.to_thread(_run)


async def chunk_audio(
    audio_path: str,
    output_dir: str,
    target_chunk_bytes: int,
    overlap_seconds: float = 5.0,
) -> list[dict]:
    """Split audio_path into chunks near target_chunk_bytes each, cutting at
    silence where possible. Returns a list of
    {"index", "path", "start_time", "end_time"} dicts in order.

    Uses -c copy (stream copy) for the split itself since audio_path is
    already transcoded to the target codec/bitrate — re-encoding a second
    time would be wasted work and additional quality loss.
    """
    if not ffmpeg_available():
        raise AudioPrepError("ffmpeg is not installed or not on PATH. See INSTALL.md.")

    total_duration = get_audio_duration(audio_path)
    total_bytes = os.path.getsize(audio_path)
    bytes_per_second = total_bytes / total_duration if total_duration else 0
    if bytes_per_second <= 0:
        raise AudioPrepError("Could not determine audio bitrate for chunking")

    target_duration = target_chunk_bytes / bytes_per_second
    silence_midpoints = detect_silence_midpoints(audio_path)

    # Build cut points: walk forward in target_duration steps, snapping each
    # to the nearest silence midpoint within 20% of the target if one exists.
    cut_points = []
    cursor = target_duration
    tolerance = target_duration * 0.2
    while cursor < total_duration:
        candidates = [m for m in silence_midpoints if abs(m - cursor) <= tolerance]
        cut = min(candidates, key=lambda m: abs(m - cursor)) if candidates else cursor
        cut_points.append(cut)
        cursor = cut + target_duration
    boundaries = [0.0] + cut_points + [total_duration]

    base = os.path.splitext(os.path.basename(audio_path))[0]
    chunks = []

    def _cut_one(index: int, seg_start: float, seg_end: float) -> dict:
        cut_start = max(0.0, seg_start - (overlap_seconds if index > 0 else 0))
        cut_end = min(total_duration, seg_end + (overlap_seconds if seg_end < total_duration else 0))
        chunk_path = os.path.join(output_dir, f"{base}_chunk{index}.mp3")
        cmd = [
            "ffmpeg", "-y",
            "-i", audio_path,
            "-ss", str(cut_start),
            "-to", str(cut_end),
            "-c", "copy",
            chunk_path,
        ]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise AudioPrepError(f"ffmpeg chunk split failed: {result.stderr[-2000:]}")
        # Report the chunk's ACTUAL cut boundaries (including pre-roll
        # overlap on non-first chunks), not the nominal segment boundaries —
        # queue.merge_chunk_results offsets local segment timestamps by
        # job.start_time, so this must be the real start of the audio file
        # on disk or every downstream absolute timestamp drifts by
        # overlap_seconds.
        return {"index": index, "path": chunk_path, "start_time": cut_start, "end_time": cut_end}

    def _run_all():
        result = []
        for i in range(len(boundaries) - 1):
            result.append(_cut_one(i, boundaries[i], boundaries[i + 1]))
        return result

    chunks = await asyncio.to_thread(_run_all)
    return chunks
