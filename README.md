# WhisperDeck

**Transcribe · Diarize · Summarize · Identify** — a local-first web app for turning audio/video into useful transcripts.

![WhisperDeck](https://img.shields.io/badge/version-0.6-blue)
![Python](https://img.shields.io/badge/python-3.11%2B-blue)

---

## Overview

WhisperDeck is a self-hosted transcription studio that runs in your browser. Upload audio or video files, get accurate transcripts with speaker labels, generate summaries, and identify known speakers — all without leaving your machine (unless you choose a cloud provider). It's multi-user (register/login, per-user data and settings) and processes everything through a background job queue with live progress, cancel/resume, and retry.

**Key capabilities:**
- **Multiple transcription backends** — local (Moonshine, faster-whisper) and cloud (Groq, OpenAI, Replicate, OpenRouter, or any OpenAI-compatible endpoint)
- **Speaker diarization** — ML-based via pyannote.audio or fast heuristic fallback
- **Voice identification** — enroll a roster of known speakers (with multiple clips each) and auto-relabel matching voices across transcripts
- **LLM-powered correction & summarization** — clean up transcripts and generate concise summaries, run as background jobs
- **Background job queue** — chunked long-file transcription, correction, summarization, re-diarization, and voice matching all run as trackable jobs with progress, cancel/resume/retry, and dismiss/clear
- **Per-user accounts** — session-based login, each user's transcripts/settings/provider keys are isolated
- **Web UI** — drag-and-drop upload, real-time progress, searchable transcript viewer
- **REST API** — scriptable endpoints for integration

---

## Quick Start (Windows)

```cmd
# 1. Install ffmpeg (required for cloud providers)
winget install Gyan.FFmpeg

# 2. Clone and enter the repo
git clone https://github.com/tito13kfm/whisperdesk.git
cd whisperdesk

# 3. Create venv & install deps (Python 3.11-3.13)
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt

# 4. Run
run.bat
# Opens http://localhost:9781
```

**Linux/macOS:**
```bash
# Install ffmpeg first (apt install ffmpeg / brew install ffmpeg)
python3.13 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python app.py
```

See [INSTALL.md](INSTALL.md) for detailed setup, including optional diarization/voice-ID extras.

---

## Features in Detail

### Authentication

WhisperDeck is multi-user. First run creates no default account — register one via the web UI (or `POST /api/register`). Sessions are cookie-based; each user's transcripts, provider API keys, and settings are scoped to their own account. The session-signing secret is auto-generated on first launch and stored locally — no env var to set.

### Transcription Providers

| Provider | Type | API Key | Models | Notes |
|----------|------|---------|--------|-------|
| **Moonshine** | Local | ❌ | `tiny`, `tiny-streaming`, `base`, `small-streaming`, `medium-streaming` | Default. English-only, fast, no GPU, beats Whisper Large on WER |
| **Built-in (faster-whisper)** | Local | ❌ | `tiny`, `base`, `small`, `medium`, `large-v3` | Multilingual. Needs `pip install faster-whisper` |
| **Groq** | Cloud | ✅ | `whisper-large-v3-flash`, `whisper-large-v3` | Free tier, hosted GPUs, best for noisy/accented audio |
| **OpenAI** | Cloud | ✅ | `whisper-1` | $0.006/min, high accuracy |
| **Replicate** | Cloud | ✅ | `whisper-large-v3-turbo` | Pay-per-run |
| **OpenRouter** | Cloud | ✅ | `openai/whisper-1`, others | Unified API for multiple providers |
| **Local / Custom** | Local | Optional | Any | Whisper.cpp, Ollama, LocalAI, or any OpenAI-compatible endpoint |

> **Default behavior:** On first launch, the Transcribe page auto-selects the first healthy provider (usually Moonshine). Switch providers in Settings → Providers.

Long recordings are automatically split into chunks and processed through the background queue (see below) instead of blocking a single request.

### Speaker Diarization

Two modes:
1. **Heuristic (default)** — assigns speakers based on pause gaps. No ML deps, fast, but unreliable with overlapping speech.
2. **pyannote.audio (recommended)** — real ML-based speaker separation.

**Enable pyannote:**
```bash
.venv\Scripts\python.exe -m pip install -r requirements-diarization.txt
```
Then:
1. Create a free HuggingFace account
2. Accept licenses for `pyannote/speaker-diarization-3.1` and `pyannote/segmentation-3.0`
3. Create a read token at https://huggingface.co/settings/tokens
4. Set `HUGGINGFACE_TOKEN` env var (or `.env` file)

**GPU acceleration (NVIDIA only on Windows):**
```bash
pip install torch --index-url https://download.pytorch.org/whl/cu121
```
AMD GPUs run CPU-only on Windows (no ROCm PyTorch build).

Re-diarization of an existing transcript runs as a background job (`rediarize`), visible on the Queue screen.

### Voice Identification

Enroll a roster of known speakers — each profile can hold multiple voice clips — and WhisperDeck can auto-relabel matching speakers across a transcript's segments via a background `voice_match` job.

Embedding backends (auto-detected, in priority order):
1. **speechbrain** — most accurate (`pip install speechbrain torchaudio`)
2. **pyannote.audio** — enabled by diarization install above
3. **librosa (MFCC)** — always available, basic fallback

### Summarization & Correction

On the transcript detail page, pick an LLM provider (Groq, OpenAI, OpenRouter, or a local Ollama-compatible endpoint) to:
- **Correct** — fix transcription errors, normalize punctuation
- **Summarize** — generate concise meeting notes

Both run as background jobs. Uses the same API keys as transcription for Groq/OpenAI/OpenRouter.

### Background Job Queue

Every long-running operation — chunked transcription, correction, summarization, re-diarization, voice matching — runs as a job on the Queue screen, with live progress, cancel/resume (chunked transcription), retry-failed-chunks, and rerun (LLM jobs). Finished jobs (completed/failed/partial/cancelled) can be dismissed individually or bulk-cleared without deleting the underlying transcript or job history.

---

## Project Structure

```
whisperdeck/
├── app.py                 # FastAPI entry point, all routes
├── run.bat                # Windows launcher (auto-detects .venv)
├── requirements.txt       # Core dependencies
├── requirements-diarization.txt  # Optional pyannote deps
├── INSTALL.md             # Detailed setup guide
├── backends/              # Transcription provider implementations
│   ├── __init__.py        # Registry & factory
│   ├── base.py            # BaseProvider abstract class
│   ├── moonshine.py       # Local Moonshine (default)
│   ├── builtin.py         # faster-whisper wrapper
│   ├── groq.py            # Groq API
│   ├── openai.py          # OpenAI API
│   ├── replicate.py       # Replicate API
│   ├── openrouter.py      # OpenRouter API
│   └── local.py           # OpenAI-compatible local endpoint
├── services/
│   ├── audio_prep.py      # ffmpeg transcoding, chunking
│   ├── auth.py            # PBKDF2 password hashing, sessions
│   ├── correction.py       # LLM transcript correction
│   ├── diarization.py     # Heuristic + pyannote diarization
│   ├── hotwords.py        # Custom vocabulary boosting
│   ├── llm_jobs.py        # Correction/summary/rediarize/voice-match jobs
│   ├── model_catalog.py   # Curated LLM model lists with live pricing
│   ├── queue.py           # Chunked-transcription background job queue
│   ├── settings.py        # Per-user settings
│   ├── transcription.py   # Inline (non-chunked) transcription pipeline
│   └── voice_id.py        # Voice enrollment & identification
├── database/
│   └── __init__.py        # SQLAlchemy models (User, Transcript, LlmJob, VoiceProfile, etc.)
└── static/                # Web UI (vanilla JS + HTML, no template engine)
    ├── index.html
    ├── rack.css
    └── rack.js
```

---

## Configuration

### Environment Variables

| Variable | Purpose | Required |
|----------|---------|----------|
| `HUGGINGFACE_TOKEN` | pyannote model access | For ML diarization |
| `PORT` | Override the bound port (default `9781`) | No |
| `WHISPERDECK_DATA_DIR` | Override where the SQLite DB and uploaded audio are stored | No (defaults to `./data`) |
| `FFMPEG_DIR` | Point at a specific ffmpeg install instead of relying on PATH | No |
| `WHISPER_CACHE_DIR` | Override the faster-whisper model cache dir (default `~/.cache/whisper`) | No |

There's no `DATABASE_URL` or `SECRET_KEY` — storage is always local SQLite under the data dir above, and the session-signing secret is generated once and saved to disk (see Authentication).

### Provider API Keys

Set in the web UI: **Settings → Providers** → paste key per provider.

Key prefixes (validated on input):
- Groq: `gsk_`
- OpenAI: `sk-`
- Replicate: `r8_`
- OpenRouter: `sk-or-`

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/` | Web UI |
| `GET` | `/api/health` | Provider/diarization status |
| `GET` | `/api/status` | App status |
| `POST` | `/api/register` | Create an account |
| `POST` | `/api/login` | Log in |
| `POST` | `/api/logout` | Log out |
| `GET` | `/api/me` | Current user |
| `GET`/`PUT` | `/api/settings` | Per-user settings |
| `GET` | `/api/providers` | List providers |
| `GET` | `/api/providers/{name}` | Provider detail |
| `GET` | `/api/providers/{name}/models` | Provider's available models |
| `PUT` | `/api/providers/{name}` | Save provider API key/config |
| `GET` | `/api/correction-models/{provider}` | LLM models available for correction/summary |
| `POST` | `/api/transcribe` | Start transcription (chunked if long) |
| `GET` | `/api/transcripts` | List transcripts |
| `GET`/`DELETE` | `/api/transcripts/{id}` | Get/delete a transcript |
| `GET` | `/api/transcripts/{id}/audio` | Stream stored audio |
| `GET` | `/api/transcripts/{id}/summary` | Get generated summary |
| `POST` | `/api/transcripts/{id}/summarize` | Enqueue a summary job |
| `POST` | `/api/transcripts/{id}/correct` | Enqueue a correction job |
| `POST` | `/api/transcripts/{id}/rediarize` | Enqueue a re-diarization job |
| `POST` | `/api/transcripts/{id}/voice-match` | Enqueue a voice-match job |
| `POST` | `/api/transcripts/{id}/retranscribe` | Re-run transcription with a different provider/model |
| `POST` | `/api/transcripts/{id}/cancel`/`resume` | Cancel/resume a chunked transcription in progress |
| `POST` | `/api/transcripts/{id}/retry-failed-chunks` | Retry failed chunks |
| `POST` | `/api/transcripts/{id}/context` | Attach a context document (hotword extraction source) |
| `POST` | `/api/transcripts/{id}/speakers/rename` | Rename a speaker label |
| `POST` | `/api/transcripts/{id}/segments/retag` | Reassign a segment's speaker |
| `POST` | `/api/transcripts/{id}/enroll-speaker` | Enroll a speaker directly from a transcript segment |
| `POST` | `/api/diarize` | Standalone diarization on an audio file |
| `GET` | `/api/jobs` | Master job queue list |
| `POST` | `/api/jobs/{id}/cancel`/`rerun`/`dismiss` | Manage an individual job |
| `POST` | `/api/jobs/clear` | Bulk-clear all finished jobs |
| `GET`/`POST` | `/api/voices` / `/api/voices/enroll` | List / enroll voice profiles |
| `POST` | `/api/voices/identify` | Identify a speaker from a clip |
| `POST` | `/api/voices/{id}/clips` | Add a clip to a roster profile |
| `GET` | `/api/voices/{id}/clips/{clip_id}/audio` | Stream a roster clip |
| `DELETE` | `/api/voices/{id}` / `/api/voices/{id}/clips/{clip_id}` | Delete a profile / clip |
| `GET`/`POST`/`DELETE` | `/api/hotwords` / `/api/hotwords/{id}` | Manage custom vocabulary |

---

## Development

### Run Tests
```bash
.venv\Scripts\python.exe -m pytest
```

### Code Style
- Type hints throughout
- Async/await for I/O
- Services are stateless functions/classes (no FastAPI deps)
- Providers implement `BaseProvider` interface

### Adding a Provider
1. Create `backends/newprovider.py` subclassing `BaseProvider`
2. Implement `transcribe()`, `check_health()`, `list_models()`
3. Register in `backends/__init__.py`'s `PROVIDER_REGISTRY`
4. Add metadata to `list_providers()`

---

## Troubleshooting

| Issue | Fix |
|-------|-----|
| `ffmpeg not found` | Install ffmpeg and restart terminal, or set `FFMPEG_DIR` |
| `moonshine-voice not installed` | `pip install moonshine-voice` (in requirements.txt) |
| `pyannote import fails` | `pip install -r requirements-diarization.txt` + set `HUGGINGFACE_TOKEN` |
| `CUDA out of memory` | Use smaller model or CPU-only torch |
| `Port 9781 in use` | Set the `PORT` env var or kill the existing process |
| `Database locked` | Ensure only one app instance runs; SQLite doesn't support concurrent writers |

---

## License

No license file yet — all rights reserved by default until one is added. This is currently a private project.

---

## Acknowledgments

- [Moonshine](https://github.com/usefulsensors/moonshine) — tiny, fast on-device ASR
- [pyannote.audio](https://github.com/pyannote/pyannote-audio) — state-of-the-art speaker diarization
- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — optimized Whisper inference
- [Groq](https://groq.com/), [OpenAI](https://openai.com/), [Replicate](https://replicate.com/), [OpenRouter](https://openrouter.ai/) — cloud inference providers
- [FastAPI](https://fastapi.tiangolo.com/), [SQLAlchemy](https://www.sqlalchemy.org/), [httpx](https://www.python-httpx.org/) — core framework deps
