"""
update_readme.py
================

Apply the screenshot references and new sections to README.md in place.
This script uses simple string replacement and avoids all the tool-call
junk that pollutes write_to_file/replace_in_file.
"""

from pathlib import Path

README = Path("README.md")
content = README.read_text(encoding="utf-8")

# 1. Hero screenshot after the intro paragraph
old = (
    "It's multi-user (register/login, per-user transcripts, settings, and API keys), "
    "and every long-running operation goes through a background job queue with live "
    "progress, cancel/resume, and retry.\n\n---"
)
new = (
    "It's multi-user (register/login, per-user transcripts, settings, and API keys), "
    "and every long-running operation goes through a background job queue with live "
    "progress, cancel/resume, and retry.\n\n"
    "![WhisperDeck Monitor — at-a-glance stats, transcript library, voice roster, "
    "and storage bargraph in the \"Signal Rack\" chassis](screenshots/01-monitor.png)\n\n---"
)
assert old in content, "intro anchor not found"
content = content.replace(old, new, 1)

# 2. Transcribe page screenshot under transcription providers
old = (
    "Long recordings are split into chunks and processed through the job queue rather "
    "than blocking a single request, so a two-hour meeting doesn't tie up the browser "
    "tab.\n\n### Speaker diarization"
)
new = (
    "Long recordings are split into chunks and processed through the job queue rather "
    "than blocking a single request, so a two-hour meeting doesn't tie up the browser "
    "tab.\n\n"
    "![Transcribe page — drag-and-drop upload, provider/model/language selectors, "
    "diarize toggle, live-capture, and the reel-to-reel deck animation](screenshots/02-transcribe.png)\n\n"
    "### Speaker diarization"
)
assert old in content, "transcribe anchor not found"
content = content.replace(old, new, 1)

# 3. Transcript detail under speaker diarization
old = (
    "You can re-diarize an existing transcript at any time; it runs as a `rediarize` "
    "job on the Queue screen.\n\n### Voice identification"
)
new = (
    "You can re-diarize an existing transcript at any time; it runs as a `rediarize` "
    "job on the Queue screen.\n\n"
    "![Transcript detail — speaker-labelled segments with per-segment audio playback, "
    "speaker rename, and the corrected/summary tabs](screenshots/04-transcript-detail.png)\n\n"
    "### Voice identification"
)
assert old in content, "diarization anchor not found"
content = content.replace(old, new, 1)

# 4. Hotwords section
old = (
    "### Hotwords and LLM correction\n\n"
    "Keep a per-user glossary of names, jargon, and product terms the model tends "
    "to mishear. You can add terms manually or paste a meeting-context document and "
    "let the app extract them. The glossary feeds the LLM correction pass that runs "
    "after transcription; it does not change the transcription itself."
)
new = (
    "### Hotwords and LLM correction\n\n"
    "Keep a per-user glossary of names, jargon, and product terms the model tends "
    "to mishear. You can add terms manually or paste a meeting-context document and "
    "let the app extract them. The glossary feeds the LLM correction pass that runs "
    "after transcription; it does not change the transcription itself.\n\n"
    "![Hotword glossary — per-user list of names, jargon, and product terms that "
    "guide the LLM correction pass](screenshots/09-hotwords.png)"
)
assert old in content, "hotwords anchor not found"
content = content.replace(old, new, 1)

# 5. Correction and summarization - add corrected screenshot
old = (
    "Both run as background jobs and reuse the API keys you already saved for "
    "transcription.\n\n### Run history and versions"
)
new = (
    "Both run as background jobs and reuse the API keys you already saved for "
    "transcription.\n\n"
    "![LLM-corrected transcript — the same source audio after a correction pass, "
    "with normalized punctuation and hotword-driven fixes](screenshots/05-corrected.png)\n\n"
    "### Run history and versions"
)
assert old in content, "correction anchor not found"
content = content.replace(old, new, 1)

# 6. Job queue screenshot
old = (
    "### Job queue\n\n"
    "The Queue screen shows every background job"
)
new = (
    "### Job queue\n\n"
    "![Job queue — live LED-bargraph progress for transcription chunks, correction, "
    "summary, rediarize, and voice-match jobs, with cancel/rerun/dismiss controls]"
    "(screenshots/06-queue.png)\n\n"
    "The Queue screen shows every background job"
)
assert old in content, "queue anchor not found"
content = content.replace(old, new, 1)

# 7. Provider API keys - add settings screenshot
old = (
    "### Provider API keys\n\n"
    "Paste them in the web UI under **Settings → Providers**. Prefixes are validated "
    "on input: Groq `gsk_`, OpenAI `sk-`, Replicate `r8_`, OpenRouter `sk-or-`."
)
new = (
    "### Provider API keys\n\n"
    "Paste them in the web UI under **Settings → Providers**. Prefixes are validated "
    "on input: Groq `gsk_`, OpenAI `sk-`, Replicate `r8_`, OpenRouter `sk-or-`.\n\n"
    "![Service panel — per-provider API keys, model pickers, and the WhisperDeck "
    "faceplate / phosphor theme controls](screenshots/08-service-panel.png)"
)
assert old in content, "api keys anchor not found"
content = content.replace(old, new, 1)

# 8. Voice identification - add voice roster screenshot (insert AFTER the section, not before)
old = (
    "Embedding backends are auto-detected in priority order: **speechbrain** (most "
    "accurate, `pip install speechbrain torchaudio`), then **pyannote.audio** (comes "
    "with the diarization install), then **librosa MFCC** (always available, basic).\n\n"
    "### Hotwords and LLM correction"
)
new = (
    "Embedding backends are auto-detected in priority order: **speechbrain** (most "
    "accurate, `pip install speechbrain torchaudio`), then **pyannote.audio** (comes "
    "with the diarization install), then **librosa MFCC** (always available, basic).\n\n"
    "![Voice roster — enrolled speaker profiles with their voice-clip samples, ready "
    "for voice-match across new transcripts](screenshots/07-voice-roster.png)\n\n"
    "### Hotwords and LLM correction"
)
assert old in content, "voice id anchor not found"
content = content.replace(old, new, 1)

# 9. Tape library screenshot - insert after the Quick Start section's portable-build line
old = (
    "[INSTALL.md](INSTALL.md) walks through all of this in detail, including the "
    "optional diarization and voice-ID extras.\n\n---\n\n## Features"
)
new = (
    "[INSTALL.md](INSTALL.md) walks through all of this in detail, including the "
    "optional diarization and voice-ID extras.\n\n"
    "![Tape library — searchable, sortable list of all transcripts with per-row "
    "open / cancel / resume / rename / delete actions](screenshots/03-tape-library.png)\n\n"
    "---\n\n## Features"
)
assert old in content, "library anchor not found"
content = content.replace(old, new, 1)

# 10. New "UI Themes" subsection - insert just before "## Configuration"
old = "\n---\n\n## Configuration"
new = (
    "\n### UI themes\n\n"
    "The \"Signal Rack\" chassis has four switchable faceplate finishes — pick one "
    "in **Service panel** (or click the faceplate knob in the top bar). Hardware "
    "behavior is identical across themes; only the chassis colors change.\n\n"
    "| | |\n"
    "|:---:|:---:|\n"
    "| ![Charcoal — default dark chassis](screenshots/10-theme-charcoal.png) | "
    "![Silverface — warm light chassis](screenshots/10-theme-silverface.png) |\n"
    "| **Charcoal** (default) | **Silverface** |\n"
    "| ![Champagne — warm cream chassis](screenshots/10-theme-champagne.png) | "
    "![Blue-Glass — deep cool chassis](screenshots/10-theme-blue-glass.png) |\n"
    "| **Champagne** | **Blue-Glass** |\n\n"
    "The oscilloscope/LED phosphor also has three color choices (Green, Cyan, Amber) "
    "in the same panel.\n\n"
    "---\n\n## Configuration"
)
assert old in content, "themes anchor not found"
content = content.replace(old, new, 1)

README.write_text(content, encoding="utf-8")
print(f"README updated ({len(content):,} chars)")
