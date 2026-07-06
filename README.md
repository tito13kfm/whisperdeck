# WhisperDeck



**Transcribe · Diarize · Summarize · Identify** — a local-first web app for turning audio/video into useful transcripts.



![WhisperDeck](https://img.shields.io/badge/version-0.6-blue)

![Python](https://img.shields.io/badge/python-3.11%2B-blue)

![License](https://img.shields.io/badge/license-MIT-green)



---



## Overview



WhisperDeck is a self-hosted transcription studio that runs in your browser. Upload audio or video files, get accurate transcripts with speaker labels, generate summaries, and identify known speakers — all without leaving your machine (unless you choose a cloud provider).



**Key capabilities:**

- **Multiple transcription backends** — local (Moonshine, Whisper.cpp, Ollama) and cloud (Groq, OpenAI, Replicate, OpenRouter)

- **Speaker diarization** — ML-based via pyannote.audio or fast heuristic fallback

- **Voice identification** — enroll known speakers and auto-label future recordings

- **LLM-powered correction & summarization** — clean up transcripts and generate concise summaries

- **Web UI** — drag-and-drop upload, real-time progress, searchable transcript viewer

- **REST API** — scriptable endpoints for integration



---



## Quick Start (Windows)



```cmd

# 1. Install ffmpeg (required for cloud providers)

winget install Gyan.FFmpeg



# 2. Clone and enter the repo

git clone https://github.com/yourusername/whisperdeck.git

cd whisperdeck



# 3. Create venv & install deps (Python 3.11–3.13)

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



---



## Features in Detail



### Transcription Providers



| Provider | Type | API Key | Models | Notes |

|----------|------|---------|--------|-------|

| **Moonshine** | Local | ❌ | `tiny`, `tiny-streaming`, `base`, `small-streaming`, `medium-streaming` | Default. English-only, fast, no GPU, beats Whisper Large on WER |

| **Built-in (Whisper Tiny)** | Local | ❌ | `tiny`, `base`, `small`, `medium`, `large-v3` | Multilingual. Needs `pip install faster-whisper` |

| **Groq** | Cloud | ✅ | `whisper-large-v3-flash`, `whisper-large-v3` | Free tier, hosted GPUs, best for noisy/accented audio |

| **OpenAI** | Cloud | ✅ | `whisper-1` | $0.006/min, high accuracy |

| **Replicate** | Cloud | ✅ | `whisper-large-v3-turbo` | Pay-per-run |

| **OpenRouter** | Cloud | ✅ | `openai/whisper-1`, others | Unified API for multiple providers |

| **Local / Custom** | Local | Optional | Any | Whisper.cpp, Ollama, LocalAI, or any OpenAI-compatible endpoint |



> **Default behavior:** On first launch, the Transcribe page auto-selects the first healthy provider (usually Moonshine). Switch providers in Settings → Providers.



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



### Voice Identification



Enroll known speakers (record a sample, give it a name) and WhisperDeck will label matching voices in future transcripts.



Backends (auto-detected, in priority order):

1. **speechbrain** — most accurate (`pip install speechbrain torchaudio`)

2. **pyannote.audio** — enabled by diarization install above

3. **librosa (MFCC)** — always available, basic fallback



### Summarization & Correction



On the transcript detail page, pick an LLM provider (Groq, OpenAI, OpenRouter, or local Ollama-compatible endpoint) to:

- **Correct** — fix transcription errors, normalize punctuation

- **Summarize** — generate concise meeting notes



Uses the same API keys as transcription for Groq/OpenAI/OpenRouter.



---



## Project Structure



```

whisperdeck/

├── app.py                 # FastAPI entry point

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

│   ├── audio_prep.py      # ffmpeg transcoding (16kHz mono MP3)

│   ├── diarization.py     # Heuristic + pyannote diarization

│   ├── hotwords.py        # Custom vocabulary boosting

│   ├── correction.py      # LLM transcript correction

│   ├── llm_jobs.py        # Summarization & correction jobs

│   ├── model_catalog.py   # Curated LLM model lists with live pricing

│   ├── auth.py            # PBKDF2 password hashing

│   └── queue.py           # Background job queue (local providers serialized)

├── database/

│   └── __init__.py        # SQLAlchemy models (User, Transcript, Speaker, etc.)

└── static/ + templates/   # Web UI (vanilla JS + Jinja2)

```



---



## Configuration



### Environment Variables



| Variable | Purpose | Required |

|----------|---------|----------|

| `HUGGINGFACE_TOKEN` | pyannote model access | For ML diarization |

| `MOONSHINE_VOICE_CACHE` | Override Moonshine model cache dir | No |

| `DATABASE_URL` | SQLAlchemy connection string | No (defaults to `sqlite:///whisperdeck.db`) |

| `SECRET_KEY` | Session signing | Auto-generated if missing |



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

| `POST` | `/api/transcribe` | Start transcription job |

| `GET` | `/api/transcripts` | List transcripts |

| `GET` | `/api/transcripts/{id}` | Get transcript detail |

| `POST` | `/api/transcripts/{id}/diarize` | Re-run diarization |

| `POST` | `/api/transcripts/{id}/summarize` | Generate summary |

| `POST` | `/api/transcripts/{id}/correct` | Correct transcript |

| `POST` | `/api/speakers/enroll` | Enroll voice sample |

| `GET` | `/api/speakers` | List enrolled speakers |

| `POST` | `/api/auth/login` | User login |

| `POST` | `/api/auth/register` | User registration |



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

3. Register in `backends/__init__.py` `PROVIDER_REGISTRY`

4. Add metadata to `list_providers()`



---



## Troubleshooting



| Issue | Fix |

|-------|-----|

| `ffmpeg not found` | Install ffmpeg and restart terminal |

| `moonshine-voice not installed` | `pip install moonshine-voice` (in requirements.txt) |

| `pyannote import fails` | `pip install -r requirements-diarization.txt` + set `HUGGINGFACE_TOKEN` |

| `CUDA out of memory` | Use smaller model or CPU-only torch |

| `Port 9781 in use` | Change port in `app.py` or kill existing process |

| `Database locked` | Ensure only one app instance runs; SQLite doesn't support concurrent writers |



---



## License



MIT — see [LICENSE](LICENSE) for details.



---



## Acknowledgments



- [Moonshine](https://github.com/usefulsensors/moonshine) — tiny, fast on-device ASR

- [pyannote.audio](https://github.com/pyannote/pyannote-audio) — state-of-the-art speaker diarization

- [faster-whisper](https://github.com/SYSTRAN/faster-whisper) — optimized Whisper inference

- [Groq](https://groq.com/), [OpenAI](https://openai.com/), [Replicate](https://replicate.com/), [OpenRouter](https://openrouter.ai/) — cloud inference providers

- [FastAPI](https://fastapi.tiangolo.com/), [SQLAlchemy](https://www.sqlalchemy.org/), [httpx](https://www.python-httpx.org/) — core framework deps

