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

(Not needed for the **Built-in** provider — it decodes locally with no
upload step.)

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

## 3. Transcription — recommended for noisy meetings / heavy accents

Default provider is **Groq**, model **whisper-large-v3** (not `-turbo` —
the non-turbo model has noticeably better accuracy on noisy audio and
accented speech; turbo trades that accuracy for speed).

1. Get a free API key at https://console.groq.com/keys
2. In the app: Settings → Providers → Groq → paste key.

No local model download or GPU needed — inference runs on Groq's hosted
GPUs. This is the recommended default over the built-in local
faster-whisper backend, which defaults to the much smaller/less accurate
`tiny` model and needs a local model download either way.

If you need fully offline/local transcription instead, switch the
Transcribe page provider to **Built-in (Whisper Tiny)** and pick a bigger
model (e.g. `large-v3`) from the model dropdown — but expect it to be slow
on CPU. That path requires `pip install faster-whisper` (not in
requirements.txt, since Groq is the recommended default).

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
