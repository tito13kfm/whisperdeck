#!/usr/bin/env python3
"""
generate_test_audio.py
======================

Generate a multi-speaker test audio file using the local Lemonade server's
Kokoro TTS model. Produces realistic meeting/test audio suitable for
diarization testing, screenshot generation, and demos.

Prerequisites
-------------
- Lemonade server running at http://localhost:13305
  (kokoro-v1 model available; see Serena memory `lemonade-server`)
- ffmpeg installed and on PATH
- Python packages: httpx

Usage
-----
    # Default: 3-speaker meeting, ~45s
    python scripts/generate_test_audio.py

    # Custom output
    python scripts/generate_test_audio.py --output screenshots/test_meeting.mp3

    # Custom dialogue (JSON)
    python scripts/generate_test_audio.py --script my_dialogue.json

    # 2 speakers
    python scripts/generate_test_audio.py --speakers 2

Dialogue format
---------------
JSON file with list of {"voice": "af_bella", "text": "..."} objects:

    [
      {"voice": "af_bella", "text": "Welcome to the meeting."},
      {"voice": "am_adam", "text": "Thanks for joining."}
    ]

Notes
-----
- Voice names MUST include suffix (af_bella, am_adam, etc.)
  Bare prefixes like "af" return HTTP 200 with 0 bytes.
- Working voices: af_bella, af_sky, af_nicole, af_sarah,
  am_adam, am_michael, bf_emma, bf_isabella, bm_george, bm_lewis
"""

import argparse
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import httpx

LEMONADE_URL = "http://localhost:13305"
TTS_MODEL = "kokoro-v1"

# Default 3-speaker meeting dialogue (Sarah/John/Emma)
DEFAULT_DIALOGUE = [
    ("af_bella", "Welcome to WhisperDeck, the self-hosted transcription studio. I'm Sarah and I'll be moderating today's meeting."),
    ("am_adam", "Thanks Sarah. I'm John, and I wanted to discuss the quarterly results with the team. The numbers look really promising."),
    ("bf_emma", "Hello everyone, I'm Emma from the engineering team. I'd like to share some updates on the new features we've been building."),
    ("am_adam", "That sounds great Emma. Before we dive in, let me remind everyone that this session is being recorded for transcription."),
    ("af_bella", "Perfect. Let's start with the engineering updates, then we'll move to the financial review and open Q and A."),
    ("bf_emma", "So we've shipped three major features this quarter. The new diarization model is significantly more accurate, and we've added support for real-time transcription."),
    ("am_adam", "Excellent work. The user feedback has been overwhelmingly positive. Revenue is up twenty percent compared to last quarter."),
    ("af_bella", "That's fantastic news. Let's open the floor for questions. Does anyone have anything they'd like to add?"),
]


def check_lemonade_available():
    """Verify Lemonade server is reachable."""
    try:
        r = httpx.get(f"{LEMONADE_URL}/v1/models", timeout=5.0)
        return r.status_code == 200
    except Exception as e:
        print(f"ERROR: Lemonade server not reachable at {LEMONADE_URL}: {e}")
        return False


def synthesize_segment(client, text, voice):
    """Call Lemonade TTS to synthesize one audio segment."""
    r = client.post(
        f"{LEMONADE_URL}/v1/audio/speech",
        json={"model": TTS_MODEL, "input": text, "voice": voice},
        timeout=60.0,
    )
    r.raise_for_status()
    if len(r.content) < 100:
        raise RuntimeError(
            f"TTS returned only {len(r.content)} bytes for voice '{voice}'. "
            f"Bare voice prefixes (e.g. 'af') silently return 0 bytes - "
            f"must use suffixed names like 'af_bella'."
        )
    return r.content


def generate_audio(dialogue, output_path, keep_segments=False):
    """
    Generate a multi-speaker audio file from a dialogue script.
    """
    output_path = Path(output_path).resolve()
    output_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"Generating {len(dialogue)} segments...")
    client = httpx.Client(timeout=120.0)

    work_dir = Path(tempfile.mkdtemp(prefix="wd_tts_"))
    segment_files = []

    try:
        for i, (voice, text) in enumerate(dialogue):
            preview = text[:60] + ("..." if len(text) > 60 else "")
            print(f"  [{i+1}/{len(dialogue)}] {voice:12s} | {preview}")
            audio_bytes = synthesize_segment(client, text, voice)
            seg_path = work_dir / f"seg_{i:03d}.mp3"
            seg_path.write_bytes(audio_bytes)
            segment_files.append(seg_path)
            print(f"           -> {seg_path.name} ({len(audio_bytes)} bytes)")

        # Concatenate with ffmpeg
        print(f"\nConcatenating {len(segment_files)} segments with ffmpeg...")
        concat_list = work_dir / "concat.txt"
        concat_list.write_text(
            "\n".join(f"file '{p.name}'" for p in segment_files)
        )

        result = subprocess.run(
            [
                "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                "-i", str(concat_list),
                "-c", "copy",
                str(output_path),
            ],
            cwd=str(work_dir),
            capture_output=True,
            text=True,
        )
        if result.returncode != 0:
            print(f"ffmpeg error:\n{result.stderr}")
            raise RuntimeError("ffmpeg concatenation failed")

        # Verify output
        if not output_path.exists() or output_path.stat().st_size < 1000:
            raise RuntimeError(f"Output file looks too small: {output_path}")

        # Get duration
        probe = subprocess.run(
            [
                "ffprobe", "-v", "quiet", "-print_format", "json",
                "-show_format", str(output_path),
            ],
            capture_output=True, text=True,
        )
        if probe.returncode == 0 and probe.stdout:
            info = json.loads(probe.stdout)
            duration = float(info.get("format", {}).get("duration", 0))
            size = output_path.stat().st_size
            print(f"\nDone! {output_path}")
            print(f"  Duration: {duration:.1f}s")
            print(f"  Size: {size:,} bytes")

        if keep_segments:
            keep_dir = output_path.parent / "_tts_segments"
            keep_dir.mkdir(exist_ok=True)
            for seg in segment_files:
                (keep_dir / seg.name).write_bytes(seg.read_bytes())
            print(f"  Segments kept in: {keep_dir}")

        return output_path

    finally:
        client.close()
        if not keep_segments:
            import shutil
            shutil.rmtree(work_dir, ignore_errors=True)


def load_dialogue_from_file(path):
    """Load dialogue from a JSON file."""
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list):
        raise ValueError("Dialogue file must contain a JSON array")
    dialogue = []
    for item in data:
        if not isinstance(item, dict) or "voice" not in item or "text" not in item:
            raise ValueError(f"Invalid dialogue entry: {item}")
        dialogue.append((item["voice"], item["text"]))
    return dialogue


def main():
    parser = argparse.ArgumentParser(
        description="Generate multi-speaker test audio via Lemonade TTS",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument(
        "--output", "-o",
        default="test_meeting.mp3",
        help="Output audio file path (default: test_meeting.mp3)",
    )
    parser.add_argument(
        "--script", "-s",
        help="Path to JSON dialogue file (overrides default)",
    )
    parser.add_argument(
        "--speakers", "-n",
        type=int, choices=[2, 3],
        help="Number of speakers for default dialogue (2 or 3)",
    )
    parser.add_argument(
        "--voices",
        nargs="+",
        help="Custom voice names (e.g. af_bella am_adam bf_emma)",
    )
    parser.add_argument(
        "--keep-segments",
        action="store_true",
        help="Keep individual segment files alongside the output",
    )

    args = parser.parse_args()

    if args.script:
        dialogue = load_dialogue_from_file(Path(args.script))
    else:
        dialogue = DEFAULT_DIALOGUE
        if args.speakers == 2 or (args.voices and len(args.voices) == 2):
            dialogue = dialogue[::2]
        if args.voices:
            n = len(args.voices)
            dialogue = [
                (args.voices[i % n], text)
                for i, (_orig_voice, text) in enumerate(dialogue)
            ]

    if not check_lemonade_available():
        print("\nMake sure Lemonade is running:")
        print("  - Server URL: http://localhost:13305")
        print("  - Model kokoro-v1 must be available")
        sys.exit(1)

    output = generate_audio(
        dialogue=dialogue,
        output_path=Path(args.output),
        keep_segments=args.keep_segments,
    )
    print(f"\nAudio saved to: {output}")


if __name__ == "__main__":
    main()