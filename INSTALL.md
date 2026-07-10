# WhisperDeck — Install / Setup

## 0. ffmpeg (required)

Before uploading to a cloud provider (Groq, OpenAI, Replicate, OpenRouter,
Local), WhisperDeck transcodes the file to 16kHz mono MP3 via ffmpeg —
this strips video tracks from `.mp4`/`.mov` uploads and shrinks long
recordings, avoiding "file too large" errors. Without ffmpeg on PATH,
transcription requests to those providers fail immediately with a clear
error telling you to install it.

```
winget install Gyan.FFmpeg
```
or `choco install ffmpeg`. Verify with `ffmpeg -version` in a new terminal.

(Not needed for the **Built-in** or **Moonshine** providers — both decode
locally with no upload step.)

## 1. Python

Use Python 3.11–3.13. **Avoid 3.14** — numpy/soundfile wheels are not
reliably available for it yet, and pip installs will fail or fall back to
slow source builds.

```
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

`run.bat` will auto-detect `.venv` and use it if present.

## 2. Run it

```
run.bat
```
or directly:
```
.venv\Scripts\python.exe app.py
```
Open http://localhost:9781

## 3. Transcription

Default provider is **Moonshine** — local, zero API key, English-only,
beats Whisper Large on word-error-rate at a fraction of the parameter
count, no GPU needed. It's in `requirements.txt`, so a plain
`pip install -r requirements.txt` gets a fully working app with no further
setup. The model for your chosen size (`tiny`, `tiny-streaming`, `base` —
default, `small-streaming`, `medium-streaming`) downloads automatically on
first transcription and is cached for subsequent runs. The Transcribe
page auto-selects the first provider whose backend actually checks out
healthy, so this is what you get on first launch.

If you need non-English audio, or want noisy-meeting/heavy-accent
accuracy that beats Moonshine, switch the Transcribe page provider to
**Groq**, model **whisper-large-v3** (not `-turbo` — the non-turbo model
has noticeably better accuracy on noisy audio and accented speech; turbo
trades that accuracy for speed):

1. Get a free API key at https://console.groq.com/keys
2. In the app: Settings → Providers → Groq → paste key.

No local model download or GPU needed — inference runs on Groq's hosted
GPUs.

There's also **Built-in (Whisper Tiny)** — local, multilingual, needs
`pip install faster-whisper` (not in requirements.txt, optional — heavier
install than Moonshine and its default `tiny` model is much less accurate;
pick a bigger model like `large-v3` from the dropdown if you use it, but
expect it to be slow on CPU).

## 4. Diarization — recommended: pyannote.audio

The default "heuristic" diarization just alternates speaker labels on
pause gaps — no real speaker separation, unreliable for real meetings.
For real ML-based diarization:

```
.venv\Scripts\python.exe -m pip install -r requirements-diarization.txt
```

pyannote's models are gated on HuggingFace — you need to accept the
license terms once, then use an access token:

1. Create a free account at https://huggingface.co
2. Accept the user conditions on both:
   - https://huggingface.co/pyannote/speaker-diarization-3.1
   - https://huggingface.co/pyannote/segmentation-3.0
3. Create a read token: https://huggingface.co/settings/tokens
4. Set it as an environment variable before running the app:
   ```
   $env:HUGGINGFACE_TOKEN = "hf_xxxxxxxxxxxx"
   ```
   (add this to your shell profile, or a `.env` file, so it persists)

Once installed and the token is set, `/api/health` will report
`"diarization_backend": true`, and transcription requests with
`diarize=true` will automatically use pyannote instead of the heuristic.

**GPU note:** pyannote runs on CPU by default here. NVIDIA GPUs can use a
CUDA build of PyTorch for much faster diarization
(`pip install torch --index-url https://download.pytorch.org/whl/cu121`).
AMD GPUs (including RDNA4 cards) have no ROCm PyTorch build for Windows,
so on Windows they run CPU-only regardless — still functional, just slower.

## 5. Voice identification (optional)

Uses the same detection pattern as diarization: `speechbrain` >
`pyannote.audio` > `librosa` (MFCC fallback) > none. Installing
`pyannote.audio` per step 4 also enables voice-ID matching. For the more
accurate speechbrain backend instead:
```
.venv\Scripts\python.exe -m pip install speechbrain torchaudio
```

## 6. Summarization

Uses whatever provider you select on the transcript detail page (Groq,
OpenAI, or a local Ollama-compatible endpoint) — same API key as
transcription for Groq/OpenAI.

## 7. Browser-based end-to-end tests (optional)

The default `pytest` suite uses FastAPI's `TestClient` — fast, no real
browser, no Chromium download. For tests that need to drive a real
browser (clicks, keyboard, file uploads, screenshots, layout), Playwright
is available as a separate optional extra:

```
.venv\Scripts\python.exe -m pip install -r requirements-browser.txt
.venv\Scripts\python.exe -m playwright install chromium
```

Run only the e2e tests (skip them in the default suite):

```
.venv\Scripts\python.exe -m pytest tests/e2e -m e2e
.venv\Scripts\python.exe -m pytest -m "not e2e"
```

The `tests/e2e/conftest.py` `live_server` fixture starts a real uvicorn
in a background thread so the browser can hit it over a real socket;
`tests/conftest.py` handles per-test data isolation (it redirects
`WHISPERDESK_DATA_DIR` to a tempdir and resets the rate-limiter).
The `tests/e2e/test_browser_smoke.py` file is a working template that
exercises the login form and the app shell — copy it for new flows.
