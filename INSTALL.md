# WhisperDeck: Install & Setup

Two ways to run it:

- **Portable (Windows):** a self-contained zip with Python and ffmpeg bundled. Build it with `powershell -ExecutionPolicy Bypass -File scripts/build_release.ps1`, unzip the result from `dist/`, run the launcher inside. Skip straight to step 3.
- **From source:** follow steps 0 to 2 below. This is the normal path for development.

The minimum working install is steps 0 to 2. Everything after that (better diarization, voice ID, browser tests) is optional and can be added later without redoing anything.

## 0. ffmpeg (required for cloud providers)

Before uploading to a cloud provider (Groq, OpenAI, Replicate, OpenRouter, Local), WhisperDeck transcodes the file to 16kHz mono MP3 via ffmpeg. That strips video tracks from `.mp4`/`.mov` uploads and shrinks long recordings, which avoids "file too large" errors. Without ffmpeg on PATH, requests to those providers fail immediately with an error telling you to install it.

```
winget install Gyan.FFmpeg
```

or `choco install ffmpeg`. Verify with `ffmpeg -version` **in a new terminal** (the PATH change doesn't reach terminals that were already open). If ffmpeg lives somewhere unusual, point the `FFMPEG_DIR` env var at its directory instead.

The **Moonshine** and **Built-in** providers decode locally with no upload step, so they work without ffmpeg.

## 1. Python and dependencies

Use Python 3.11, 3.12, or 3.13. **Avoid 3.14**: numpy/soundfile wheels aren't reliably available for it yet, so pip either fails or falls back to slow source builds.

```
py -3.13 -m venv .venv
.venv\Scripts\python.exe -m pip install --upgrade pip
.venv\Scripts\python.exe -m pip install -r requirements.txt
```

## 2. Run it

```
run.bat
```

or directly:

```
.venv\Scripts\python.exe app.py
```

Open http://localhost:9781 and register an account. **The first account registered becomes the admin** (it can manage users and mint password-reset tokens), so register yours before exposing the app to anyone else.

`run.bat` auto-detects `.venv` and re-checks dependencies on every launch. Set the `PORT` env var if 9781 is taken.

## 3. Transcription

You already have a working transcription setup at this point. The default provider is **Moonshine**: local, no API key, English-only, no GPU needed, and it beats Whisper Large on word-error-rate at a fraction of the parameter count. The model for your chosen size (`tiny`, `tiny-streaming`, `base` (default), `small-streaming`, `medium-streaming`) downloads automatically on first transcription and is cached after that. The Transcribe page auto-selects the first provider that reports healthy, which on a fresh install is Moonshine.

**When to add Groq:** non-English audio, or noisy-meeting/heavy-accent accuracy beyond what Moonshine gives you. Use model **whisper-large-v3**, not `-turbo` or `-flash`; the full model is noticeably more accurate on hard audio, the fast variants trade that away for speed.

1. Get a free API key at https://console.groq.com/keys
2. In the app: Settings → Providers → Groq → paste key.

Inference runs on Groq's hosted GPUs, so there's no local download and no GPU requirement.

**Built-in (faster-whisper)** is a third option: local and multilingual, but heavier than Moonshine and not installed by default (`pip install faster-whisper`). Its default `tiny` model is much less accurate; if you use this provider, pick `large-v3` from the model dropdown and expect it to be slow on CPU.

## 4. Diarization (recommended: pyannote.audio)

The default "heuristic" diarization just alternates speaker labels on pause gaps. There's no real speaker separation, and it falls apart on real meetings. For ML diarization:

```
.venv\Scripts\python.exe -m pip install -r requirements-diarization.txt
```

pyannote's models are gated on HuggingFace; you accept the license terms once, then authenticate with a token:

1. Create a free account at https://huggingface.co
2. Accept the user conditions on both model pages:
   - https://huggingface.co/pyannote/speaker-diarization-3.1
   - https://huggingface.co/pyannote/segmentation-3.0
3. Create a read token: https://huggingface.co/settings/tokens
4. Set it before running the app:
   ```
   $env:HUGGINGFACE_TOKEN = "hf_xxxxxxxxxxxx"
   ```
   Add that to your shell profile or a `.env` file so it persists.

Once the install and token are in place, `/api/health` reports `"diarization_backend": true` and transcriptions with `diarize=true` use pyannote automatically instead of the heuristic.

**GPU note:** pyannote runs on CPU by default. NVIDIA cards can use a CUDA PyTorch build for much faster diarization (`pip install torch --index-url https://download.pytorch.org/whl/cu121`). AMD cards (including RDNA4) have no ROCm PyTorch build on Windows, so they run CPU-only regardless. Still functional, just slower.

## 5. Voice identification (optional)

Voice ID auto-detects its embedding backend in priority order: `speechbrain` > `pyannote.audio` > `librosa` (MFCC fallback). Installing pyannote in step 4 already enables decent voice matching. For the more accurate speechbrain backend:

```
.venv\Scripts\python.exe -m pip install speechbrain torchaudio
```

## 6. Correction and summarization

No setup of their own. Both use whichever LLM provider you select on the transcript detail page (Groq, OpenAI, OpenRouter, or a local Ollama-compatible endpoint), with the same API keys you saved for transcription.

## 7. Browser-based end-to-end tests (optional, dev only)

The default `pytest` suite uses FastAPI's `TestClient`: fast, no real browser, no Chromium download. For tests that drive a real browser (clicks, keyboard, uploads, screenshots), Playwright is a separate extra:

```
.venv\Scripts\python.exe -m pip install -r requirements-browser.txt
.venv\Scripts\python.exe -m playwright install chromium
```

Run the two suites separately:

```
.venv\Scripts\python.exe -m pytest tests/e2e -m e2e     # browser tests only
.venv\Scripts\python.exe -m pytest -m "not e2e"          # everything else
```

How the harness works: the `live_server` fixture in `tests/e2e/conftest.py` starts a real uvicorn in a background thread so the browser hits a real socket, `tests/conftest.py` redirects `WHISPERDECK_DATA_DIR` to a tempdir so no test touches real data, and `tests/e2e/conftest.py`'s `pytest_runtest_setup` clears the rate limiter before every e2e test (the parent conftest resets it too, but only inside its `client` fixture, which e2e tests don't use). `tests/e2e/test_browser_smoke.py` is a working template covering the login form and app shell; copy it for new flows.

Any e2e test that intercepts network traffic (`page.route`, `page.expect_request`, `page.expect_response`) must take the `page_no_sw` fixture instead of `page`. `static/sw.js` reissues every `/api/*` request from the service worker's own scope, so a page-level route handler never sees it: the stub is ignored, the real response comes back, and the assertion can pass against the wrong state.

## 8. If you get locked out

An admin can mint a one-time password-reset token from the UI for any user. If the admin account itself is the problem, reset any password directly against the database:

```
.venv\Scripts\python.exe scripts\reset_password.py --username <name> --new-password <pass>
```

Pass `--data-dir` (or set `WHISPERDECK_DATA_DIR`) if your data lives somewhere other than `./data`.
