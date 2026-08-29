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
import uuid


class AudioPrepError(Exception):
    """Raised when ffmpeg is unavailable or the transcode fails."""
    pass


def _ffmpeg_bin() -> str:
    """Resolve the ffmpeg binary: bundled copy (portable build) if
    FFMPEG_DIR is set, otherwise PATH lookup (normal dev/installed use)."""
    ffmpeg_dir = os.environ.get("FFMPEG_DIR")
    return os.path.join(ffmpeg_dir, "ffmpeg.exe") if ffmpeg_dir else "ffmpeg"


def _ffprobe_bin() -> str:
    ffmpeg_dir = os.environ.get("FFMPEG_DIR")
    return os.path.join(ffmpeg_dir, "ffprobe.exe") if ffmpeg_dir else "ffprobe"


def ffmpeg_available() -> bool:
    ffmpeg_dir = os.environ.get("FFMPEG_DIR")
    if ffmpeg_dir:
        return os.path.isfile(_ffmpeg_bin())
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
    suffix = uuid.uuid4().hex
    output_path = os.path.join(output_dir, f"{base}_{suffix}_16k.mp3")

    cmd = [
        _ffmpeg_bin(), "-y",
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


async def transcode_stereo_for_diarization(input_path: str, output_dir: str) -> str:
    """16 kHz 2-channel FLAC copy of a live-capture recording, kept solely
    for channel-aware diarization (mic = channel 0, system audio = channel 1).
    The mono mp3 from transcode_for_upload stays the transcription source;
    FLAC because libsndfile reads it natively (it cannot open webm)."""
    if not ffmpeg_available():
        raise AudioPrepError(
            "ffmpeg is not installed or not on PATH. It's required to build "
            "the stereo diagnostic copy used for channel-aware diarization. "
            "See INSTALL.md."
        )

    base = os.path.splitext(os.path.basename(input_path))[0]
    suffix = uuid.uuid4().hex
    output_path = os.path.join(output_dir, f"{base}_{suffix}_16k_stereo.flac")

    cmd = [
        _ffmpeg_bin(), "-y",
        "-i", input_path,
        "-vn",
        "-ac", "2",
        "-ar", "16000",
        "-c:a", "flac",
        output_path,
    ]

    def _run():
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise AudioPrepError(f"ffmpeg stereo transcode failed: {result.stderr[-2000:]}")
        return output_path

    return await asyncio.to_thread(_run)


def get_audio_duration(audio_path: str) -> float:
    """Return the audio file's duration in seconds via ffprobe."""
    result = subprocess.run(
        [_ffprobe_bin(), "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", audio_path],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise AudioPrepError(f"ffprobe failed to read duration: {result.stderr[-500:]}")
    return float(result.stdout.strip())


def has_video_stream(path: str) -> bool:
    """True if the file has at least one real (non-cover-art) video
    stream — used to decide whether to retain the original upload for
    playback, independent of file extension (a misnamed or audio-only
    .mp4 must report False).

    Embedded cover art (ID3 APIC frames, voice-memo/podcast thumbnails)
    shows up to ffprobe as a video stream too, but flagged with the
    attached_pic disposition — those must NOT count as video, or a plain
    audio file with album art would trigger the video-playback UI for a
    static thumbnail. Querying stream_disposition=attached_pic alongside
    codec_type distinguishes the two: each video stream is reported as
    one CSV row "video,0" (real video) or "video,1" (attached pic)."""
    try:
        result = subprocess.run(
            [_ffprobe_bin(), "-v", "error", "-select_streams", "v",
             "-show_entries", "stream=codec_type:stream_disposition=attached_pic",
             "-of", "csv=p=0", path],
            capture_output=True, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    if result.returncode != 0:
        return False
    for line in result.stdout.strip().splitlines():
        fields = line.split(",")
        if len(fields) >= 2 and fields[0] == "video" and fields[1] != "1":
            return True
    return False


_SILENCE_END_RE = re.compile(r"silence_end:\s*([\d.]+)\s*\|\s*silence_duration:\s*([\d.]+)")

_MAX_VOLUME_RE = re.compile(r"max_volume:\s*([-\d.]+|n/a|-?inf)\s*dB", re.IGNORECASE)


def is_silent_audio(audio_path: str, threshold_db: float = -50.0) -> bool:
    """Return True if audio_path is silent (peak volume below threshold).

    Uses ffmpeg's volumedetect filter — decodes the file and measures peak
    volume. Pure digital silence reports -91 dB or 'n/a' (treated as silent).
    Speech typically peaks around -20 to -10 dB, so -50 dB cleanly separates.

    On any ffmpeg failure (missing binary, unreadable file) returns False
    (fail-open) so a transient tool error never silently drops content.
    """
    try:
        result = subprocess.run(
            [_ffmpeg_bin(), "-i", audio_path, "-af", "volumedetect", "-vn", "-sn", "-f", "null", "-"],
            capture_output=True, text=True,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    # volumedetect writes to stderr; non-zero exit for some containers is
    # still ok — the stderr may contain the measurement.
    output = result.stderr or ""
    match = _MAX_VOLUME_RE.search(output)
    if not match:
        # No measurement produced (e.g. ffmpeg couldn't decode) — don't
        # assume silence, let the provider decide.
        return False
    raw = match.group(1).strip().lower()
    if raw == "n/a":
        return True
    try:
        peak_db = float(raw)
    except ValueError:
        return False
    return peak_db < threshold_db


def detect_silence_gaps(
    audio_path: str, noise_db: str = "-30dB", min_duration: float = 0.5, strict: bool = False
) -> list[dict]:
    """Return each detected silence gap as {"start", "end", "duration"}
    (seconds), in order.

    A single-pass ffmpeg filter — no decode-to-file, no ML model, adds low
    single-digit seconds even on a multi-hour recording.

    detect_silence_midpoints() below is the chunk-splitting view of this same
    scan; this one keeps the gap boundaries the midpoint average throws away,
    which is what a caller reasoning about pause *structure* (rather than
    where to cut) needs.

    strict decides what a failed probe means, because the two callers need
    opposite answers and an empty list cannot tell them apart:

    - strict=False (default, and what chunk_audio has always relied on): a
      nonzero ffmpeg exit yields an empty list, same as a recording with no
      gaps. chunk_audio degrades to fixed-interval cuts on an empty list, so
      silence is the safe failure mode there.
    - strict=True: a nonzero exit raises AudioPrepError. An eligibility caller
      must not read "the probe broke" as "this recording has no pauses at
      all" — that inference vetoes a long recording on the strength of a tool
      failure.
    """
    result = subprocess.run(
        [
            _ffmpeg_bin(), "-i", audio_path,
            "-af", f"silencedetect=noise={noise_db}:d={min_duration}",
            "-f", "null", "-",
        ],
        capture_output=True, text=True,
    )
    if strict and result.returncode != 0:
        raise AudioPrepError(f"ffmpeg silencedetect failed: {result.stderr[-500:]}")
    gaps = []
    for match in _SILENCE_END_RE.finditer(result.stderr):
        silence_end = float(match.group(1))
        silence_duration = float(match.group(2))
        gaps.append({
            "start": silence_end - silence_duration,
            "end": silence_end,
            "duration": silence_duration,
        })
    return gaps


def detect_silence_midpoints(audio_path: str, noise_db: str = "-30dB", min_duration: float = 0.5) -> list[float]:
    """Return timestamps (seconds) at the midpoint of each detected silence
    gap, in order. Cutting a chunk boundary at one of these midpoints keeps
    the cut roughly equidistant from speech on either side.

    A single-pass ffmpeg filter — no decode-to-file, no ML model, adds low
    single-digit seconds even on a multi-hour recording.
    """
    return [
        (gap["start"] + gap["end"]) / 2
        for gap in detect_silence_gaps(audio_path, noise_db=noise_db, min_duration=min_duration)
    ]


# Diarization eligibility pre-pass (design decision 1 of
# docs/superpowers/specs/2026-08-01-studio-classification-design.md).
#
# Both thresholds are deliberately conservative: the pre-pass is an opt-out
# gate with no ground truth to calibrate against, so it only vetoes recordings
# whose audio shape makes speaker separation meaningless, never ones that
# merely look single-speaker. Anything it isn't sure about stays eligible.
MIN_DIARIZATION_DURATION_SECONDS = 30.0
MAX_CONTINUOUS_SPEECH_SECONDS = 300.0


class DiarizationEligibility:
    """Result of the diarization pre-pass: whether the audio is worth
    diarizing, plus a human-readable reason suitable for an API rejection
    message (empty when eligible)."""

    __slots__ = ("eligible", "reason")

    def __init__(self, eligible: bool, reason: str = ""):
        self.eligible = eligible
        self.reason = reason

    def __repr__(self) -> str:
        return f"DiarizationEligibility(eligible={self.eligible!r}, reason={self.reason!r})"


def evaluate_diarization_eligibility(
    audio_path: str,
    *,
    min_duration_seconds: float = MIN_DIARIZATION_DURATION_SECONDS,
    max_continuous_speech_seconds: float = MAX_CONTINUOUS_SPEECH_SECONDS,
) -> DiarizationEligibility:
    """Decide whether a recording is worth diarizing, from audio features
    only — no transcript text, no ML, no classification input.

    Two narrow vetoes, both computed from machinery this module already has:

    (a) Below min_duration_seconds. A clip that short carries too little
        speech for speaker clustering to separate anything meaningfully.
    (b) A continuous speech run longer than max_continuous_speech_seconds
        with no pause at all. Conversational turn-taking cannot happen
        without pauses, so an unbroken stretch that long is a single
        continuous source, not a discussion.

    Everything else is eligible. So is any recording whose probes fail:
    ffmpeg being unavailable or a container being unreadable is a tool
    problem, and failing open leaves today's behavior intact rather than
    silently switching diarization off (same fail-open reasoning as
    is_silent_audio above).
    """
    try:
        duration = get_audio_duration(audio_path)
    except (AudioPrepError, OSError, ValueError, subprocess.SubprocessError):
        return DiarizationEligibility(True)

    if duration < min_duration_seconds:
        return DiarizationEligibility(
            False,
            f"Recording is too short to diarize ({duration:.1f}s, "
            f"minimum {min_duration_seconds:.0f}s)",
        )

    try:
        gaps = detect_silence_gaps(audio_path, strict=True)
    except (AudioPrepError, OSError, ValueError, subprocess.SubprocessError):
        return DiarizationEligibility(True)

    # Speech runs are the spans between gaps, including the head (start of
    # file to the first gap) and the tail (last gap to end of file). With no
    # gaps at all the whole recording is one run.
    longest_run = 0.0
    cursor = 0.0
    for gap in gaps:
        longest_run = max(longest_run, gap["start"] - cursor)
        cursor = gap["end"]
    longest_run = max(longest_run, duration - cursor)

    if longest_run > max_continuous_speech_seconds:
        return DiarizationEligibility(
            False,
            f"Recording has {longest_run / 60:.1f} minutes of unbroken speech "
            "with no pauses — that's a continuous single source, not a conversation",
        )

    return DiarizationEligibility(True)


async def evaluate_diarization_eligibility_async(audio_path: str, **kwargs) -> DiarizationEligibility:
    """Await evaluate_diarization_eligibility() off the event loop.

    Both guard sites are async request handlers, and the eligibility probe is
    two ffmpeg/ffprobe subprocesses — the silencedetect one decodes the whole
    file. Running that inline would stall every other request on the loop for
    its duration, so it goes to a worker thread the same way
    transcode_for_upload() does.
    """
    return await asyncio.to_thread(evaluate_diarization_eligibility, audio_path, **kwargs)


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
    suffix = uuid.uuid4().hex

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
        list_path = os.path.join(output_dir, f"{base}_{suffix}_seed_list.txt")
        out_path = os.path.join(output_dir, f"{base}_{suffix}_seed.wav")
        try:
            for i, (start, end) in enumerate(selected):
                part = os.path.join(output_dir, f"{base}_{suffix}_seed_part{i}.wav")
                result = subprocess.run(
                    [
                        _ffmpeg_bin(), "-y", "-i", audio_path,
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
                [_ffmpeg_bin(), "-y", "-f", "concat", "-safe", "0", "-i", list_path, "-c", "copy", out_path],
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


async def extract_segment_clips(
    audio_path: str,
    clips: list[dict],
    output_dir: str,
    batch_size: int = 20,
) -> list[str | None]:
    """Extract each {start, end} range into its own 16kHz mono WAV.

    Unlike ``extract_clips_concat`` (which joins them into one file for
    roster enrollment), this returns one path per input clip — the layout
    ``voice_match`` needs so each segment can be identified independently.
    Batching reduces the number of ``get_audio_duration`` calls from O(n)
    to O(n / batch_size) and groups temp-file lifecycle.

    Each resulting WAV must be deleted by the caller when done (mirrors
    ``extract_clips_concat``'s contract). A per-clip extraction failure
    yields ``None`` at that index rather than aborting the whole batch —
    the caller skips it the same way the old per-segment loop skipped
    ``AudioPrepError``.
    """
    if not ffmpeg_available():
        raise AudioPrepError("ffmpeg is not installed or not on PATH. See INSTALL.md.")
    if not clips:
        return []

    duration = get_audio_duration(audio_path)
    base = os.path.splitext(os.path.basename(audio_path))[0]
    suffix = uuid.uuid4().hex

    validated: list[tuple[float, float] | None] = []
    for clip in clips:
        try:
            start = max(0.0, float(clip["start"]))
            end = min(duration, float(clip["end"]))
        except (TypeError, ValueError, KeyError):
            validated.append(None)
            continue
        if end <= start:
            validated.append(None)
            continue
        validated.append((start, end))

    results: list[str | None] = [None] * len(clips)

    for batch_start in range(0, len(validated), batch_size):
        batch_slice = validated[batch_start: batch_start + batch_size]

        def _run_batch() -> list[str | None]:
            batch_paths: list[str | None] = [None] * len(batch_slice)
            for offset, item in enumerate(batch_slice):
                if item is None:
                    continue
                start, end = item
                part = os.path.join(
                    output_dir, f"{base}_{suffix}_seg{batch_start + offset}.wav"
                )
                result = subprocess.run(
                    [
                        _ffmpeg_bin(), "-y", "-i", audio_path,
                        "-ss", str(start), "-to", str(end),
                        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le",
                        part,
                    ],
                    capture_output=True,
                    text=True,
                )
                if result.returncode != 0:
                    try:
                        os.remove(part)
                    except OSError:
                        pass
                    continue
                batch_paths[offset] = part
            return batch_paths

        batch_out = await asyncio.to_thread(_run_batch)
        for offset, p in enumerate(batch_out):
            results[batch_start + offset] = p

    return results


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
    suffix = uuid.uuid4().hex
    chunks = []

    def _cut_one(index: int, seg_start: float, seg_end: float) -> dict:
        cut_start = max(0.0, seg_start - (overlap_seconds if index > 0 else 0))
        cut_end = min(total_duration, seg_end + (overlap_seconds if seg_end < total_duration else 0))
        chunk_path = os.path.join(output_dir, f"{base}_{suffix}_chunk{index}.mp3")
        cmd = [
            _ffmpeg_bin(), "-y",
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
    filtered = []
    for c in chunks:
        if is_silent_audio(c["path"]):
            try:
                os.remove(c["path"])
            except OSError:
                pass
            continue
        filtered.append(c)
    for new_idx, c in enumerate(filtered):
        c["index"] = new_idx
    return filtered
