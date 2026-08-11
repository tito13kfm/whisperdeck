"""Audio cleanup stage — unified pre-transcription processing pipeline.

Design (issue #270, per docs/superpowers/specs/...studio-classification-design.md
decision 10): loudnorm/denoise (#236) -> VAD (#237) -> chunking -> transcribe
-> post-hoc hallucination filter (#238), with Demucs (#239) as a separate
opt-in pre-step for noisy local recordings. Each step is independently opt-in
with a safe fallback to the original audio on failure.
"""
import os
import subprocess
import asyncio
import uuid
from dataclasses import dataclass, field


@dataclass
class CleanupResult:
    audio_path: str
    applied_steps: list[str] = field(default_factory=list)
    skipped_steps: list[str] = field(default_factory=list)
    failed_steps: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


class CleanupError(Exception):
    pass


def _ffmpeg_bin() -> str:
    ffmpeg_dir = os.environ.get("FFMPEG_DIR")
    return os.path.join(ffmpeg_dir, "ffmpeg.exe") if ffmpeg_dir else "ffmpeg"


def _ffmpeg_available() -> bool:
    import shutil
    ffmpeg_dir = os.environ.get("FFMPEG_DIR")
    if ffmpeg_dir:
        return os.path.isfile(_ffmpeg_bin())
    return shutil.which("ffmpeg") is not None


async def cleanup_audio(
    audio_path: str,
    output_dir: str,
    user_settings: dict,
) -> CleanupResult:
    """Run the opt-in audio-cleanup chain on audio_path, returning the path to
    the (possibly processed) audio and a CleanupResult describing what ran.

    Falls back to the original audio_path on any filter failure — cleanup
    never blocks transcription. If no cleanup steps are enabled, returns the
    original path with only skipped_steps populated.
    """
    result = CleanupResult(audio_path=audio_path)

    loudnorm_enabled = user_settings.get("cleanup_loudnorm_enabled", False)
    highpass_enabled = user_settings.get("cleanup_highpass_enabled", False)
    denoise_enabled = user_settings.get("cleanup_denoise_enabled", False)

    any_filter = loudnorm_enabled or highpass_enabled or denoise_enabled
    if not any_filter:
        result.skipped_steps = ["loudnorm", "highpass", "denoise"]
        return result

    if not _ffmpeg_available():
        result.skipped_steps = ["loudnorm", "highpass", "denoise"]
        result.warnings.append("ffmpeg not available — skipping audio cleanup")
        return result

    base = os.path.splitext(os.path.basename(audio_path))[0]
    suffix = uuid.uuid4().hex
    cleaned_path = os.path.join(output_dir, f"{base}_{suffix}_clean.mp3")

    cmd = [_ffmpeg_bin(), "-y", "-i", audio_path]

    filter_parts = []
    applied = []
    skipped = []
    failed = []

    if loudnorm_enabled:
        target = user_settings.get("cleanup_loudnorm_target", -23.0)
        filter_parts.append(f"loudnorm=I={target}:TP=-1.5:LRA=11:linear=true")
        applied.append("loudnorm")

    if highpass_enabled:
        filter_parts.append("highpass=f=80")
        applied.append("highpass")

    if denoise_enabled:
        filter_parts.append("afftdn")
        applied.append("denoise")

    if not filter_parts:
        skipped.extend(["loudnorm", "highpass", "denoise"])
        result.skipped_steps = skipped
        return result

    af = ",".join(filter_parts)
    cmd.extend([
        "-vn", "-ac", "1", "-ar", "16000",
        "-af", af,
        "-c:a", "libmp3lame", "-b:a", "128k",
        cleaned_path,
    ])

    def _run():
        proc = subprocess.run(cmd, capture_output=True, text=True)
        if proc.returncode != 0:
            raise CleanupError(f"ffmpeg cleanup failed: {proc.stderr[-2000:]}")
        return cleaned_path

    try:
        out = await asyncio.to_thread(_run)
    except (CleanupError, OSError) as e:
        failed = applied.copy()
        result.failed_steps = failed
        result.warnings.append(str(e))
        return result

    result.audio_path = out
    result.applied_steps = applied
    return result


def filter_hallucinations(
    segments: list[dict],
    *,
    rep_window: int = 3,
    logprob_cutoff: float = -2.0,
    no_speech_cutoff: float = 0.6,
) -> list[dict]:
    """Post-hoc hallucination filter over segment output.

    Flags segments matching a repeated-ngram + low-confidence pattern as
    likely hallucinations. Segments flagged for removal are stripped from
    the returned list; split segments (where only the repeated suffix was
    trimmed) have their text truncated.

    Returns the filtered segment list. Only acts on builtin/faster-whisper
    segments that carry avg_logprob (mapped to 'confidence') and
    no_speech_prob fields.
    """
    if not segments or rep_window < 2:
        return segments

    def _ngrams(tokens, n):
        return [tuple(tokens[i:i + n]) for i in range(len(tokens) - n + 1)]

    filtered = []
    for seg in segments:
        text = seg.get("text", "")
        conf = seg.get("confidence")  # avg_logprob from faster-whisper
        nsp = seg.get("no_speech_prob")
        tokens = text.lower().split()

        is_low_conf = conf is not None and conf < logprob_cutoff
        is_high_nsp = nsp is not None and nsp > no_speech_cutoff

        if len(tokens) < rep_window or (not is_low_conf and not is_high_nsp):
            filtered.append(seg)
            continue

        # Check for repeated n-grams — hallmark of Whisper hallucination.
        ng = _ngrams(tokens, rep_window)
        repeated = _find_longest_repeat(ng)
        if repeated is None:
            filtered.append(seg)
            continue

        # Segment is suspect — drop entirely rather than trying to salvage
        # a partial line, since the repetition makes it unreliable.
        pass

    return filtered


def _find_longest_repeat(ngrams: list[tuple]) -> int | None:
    """Return the length of the longest repeated-ngram run, or None if no
    repetition is detected. A run counts consecutive identical n-grams."""
    if not ngrams:
        return None
    max_run = 1
    current_run = 1
    for i in range(1, len(ngrams)):
        if ngrams[i] == ngrams[i - 1]:
            current_run += 1
            if current_run > max_run:
                max_run = current_run
        else:
            current_run = 1
    return max_run if max_run >= 2 else None


async def cleanup_demucs(
    audio_path: str,
    output_dir: str,
    user_settings: dict,
) -> str:
    """Run Demucs vocal isolation on audio_path. Returns the path to the
    separated vocals file, or the original audio_path if Demucs is disabled,
    unavailable, or fails.

    Demucs is local-only: it downloads a multi-GB PyTorch model on first use
    and is meaningfully slow on CPU. Only useful for noisy local recordings
    where the builtin/moonshine transcription quality suffers from background
    noise or music.
    """
    enabled = user_settings.get("cleanup_demucs_enabled", False)
    if not enabled:
        return audio_path

    try:
        import demucs.separate
    except ImportError:
        return audio_path

    base = os.path.splitext(os.path.basename(audio_path))[0]
    suffix = uuid.uuid4().hex
    out_dir = os.path.join(output_dir, f"{base}_{suffix}_demucs")

    def _run():
        # Demucs writes vocals.wav into out_dir/htdemucs/<filename>/
        demucs.separate.main(["--two-stems", "vocals", "-o", out_dir, audio_path])
        vocals_dir = os.path.join(out_dir, "htdemucs", base)
        vocals_path = os.path.join(vocals_dir, "vocals.wav")
        if not os.path.isfile(vocals_path):
            raise CleanupError("Demucs completed but vocals.wav not found")
        return vocals_path

    try:
        return await asyncio.to_thread(_run)
    except Exception:
        return audio_path
