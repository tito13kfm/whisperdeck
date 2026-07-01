"""Audio preprocessing — normalize uploads before sending to cloud providers.

Video files (mp4, mov, ...) and long recordings can exceed a provider's
upload size limit. Whisper-family models run at 16kHz mono internally
regardless of input format, so transcoding down to that loses nothing the
model would have used anyway, while shrinking the upload substantially and
stripping any video track.
"""
import asyncio
import os
import shutil
import subprocess


class AudioPrepError(Exception):
    """Raised when ffmpeg is unavailable or the transcode fails."""
    pass


def ffmpeg_available() -> bool:
    return shutil.which("ffmpeg") is not None


async def transcode_for_upload(input_path: str, output_dir: str) -> str:
    """Transcode to 16kHz mono MP3 and return the new file path.

    Streams through ffmpeg rather than decoding into memory, so multi-hour
    recordings don't spike RAM. Does not attempt to handle files that still
    exceed the provider's limit after transcoding (very long meetings) —
    that needs chunking, which this does not do.
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
        "-b:a", "64k",
        output_path,
    ]

    def _run():
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise AudioPrepError(f"ffmpeg transcode failed: {result.stderr[-2000:]}")
        return output_path

    return await asyncio.to_thread(_run)
