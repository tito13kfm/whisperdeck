# Moonshine Local Provider — Design Spec

**Goal:** Add Moonshine (https://github.com/moonshine-ai/moonshine, pip package `moonshine-voice`) as a new, selectable local transcription provider, alongside the existing `builtin` (faster-whisper) provider — not a replacement. Both providers are wired in and user-selectable so the user can A/B test them and pick whichever gives better real-world results; there is no forced default winner between the two.

**Background:** The existing `builtin` provider (faster-whisper `tiny`) is the app's only local/offline transcription option, and per its own provider metadata is only "great for quick dictation," not real workflow quality. faster-whisper is not currently installed. Moonshine is a fully on-device, MIT-licensed, CPU-only ASR model family that reportedly beats Whisper Large on WER for its supported languages at a fraction of the parameter count. The user's real usage is English-only, so Moonshine's narrow 8-language support (English, Spanish, Mandarin, Japanese, Korean, Vietnamese, Ukrainian, Arabic) is not a limiting factor.

Confirmed during design: `pip install moonshine-voice` resolves cleanly against this project's Python 3.13 venv with a CPU-only dependency set (`librosa`, `soundfile`, `onnxruntime`, no torch/CUDA requirement beyond what's already installed for other providers). The package ships `Transcriber` / `transcribe_without_streaming()` for file-based (non-live) use.

## Scope

**In scope:**
- New `backends/moonshine.py` provider implementing the existing `BaseProvider` interface.
- Registration in `backends/__init__.py`'s `PROVIDER_REGISTRY` and `list_providers()` metadata, under id `"moonshine"`.
- English-only model sizes exposed in the model list: `tiny` (26M, 12.66% WER), `tiny-streaming` (34M, 12.00% WER), `base` (58M, 10.07% WER — **default**), `small-streaming` (123M, 7.84% WER), `medium-streaming` (245M, 6.65% WER).
- Auto-download of model files on first use of a given size, mirroring `builtin.py`'s existing "auto-download from HuggingFace on first use" UX.
- `INSTALL.md` update documenting the new provider and its setup (matches memory note that this file would need touching).

**Explicitly out of scope:**
- The other 7 languages' Base-architecture models (Spanish, Mandarin, Japanese, Korean, Vietnamese, Ukrainian, Arabic) — English-only usage makes these dead weight in the dropdown for now.
- Replacing or modifying `builtin.py` / faster-whisper in any way — it stays exactly as-is.
- GPU/DirectML acceleration — separate parked idea (`project_whisperdesk_windows_ml_pivot_idea`), not coupled to this work.
- The whisper-tiny/local pre-pass idea for prompt optimization — explicitly blocked on this work landing and the user picking a winner between `builtin` and `moonshine`, so the pre-pass can be built on whichever local model wins rather than risk redoing it.
- Word-level timestamps — Moonshine exposes only line-level `start_time`/`duration`, not word-level. Nothing else in the app currently depends on word-level granularity, so this is a known, accepted limitation, not a blocker.
- Moonshine's experimental built-in speaker diarization — its own docs call it unreliable; the existing pyannote.audio diarization path is unaffected and remains the only diarization path.

## Architecture

`backends/moonshine.py` follows the exact same shape as `backends/builtin.py`:
- Constructor takes `config: dict`, reads `default_model` (falls back to `"base"`) same pattern as every other provider (`config.get("default_model") or "base"`, using `or` not `.get`'s default arg, so a saved-but-empty `default_model` doesn't shadow the fallback — same bug class already fixed once in `groq.py`).
- `async def transcribe(self, audio_path: str, **kwargs) -> TranscriptionResult` — same signature as every other provider.
- `async def check_health(self) -> dict` — for local providers this should confirm the package/model can actually load, not just return `{"ok": True}` unconditionally (mirror whatever check `builtin.py` currently does, if any; if `builtin.py` has no real health check, at minimum confirm the `moonshine_voice` import succeeds).
- `async def list_models(self) -> list[str]` — returns the English-only `SUPPORTED_MODELS` list (see Scope).

Registration:
- `backends/__init__.py`: import `MoonshineProvider`, add `"moonshine": MoonshineProvider` to `PROVIDER_REGISTRY`, add an entry to `list_providers()`:
  ```python
  {
      "id": "moonshine",
      "name": "Moonshine",
      "description": "Local · no API key · lightweight on-device ASR",
      "default_model": "base",
      "needs_key": False,
      "key_prefix": "",
      "zero_setup": True,
  }
  ```
  (matches the `builtin` entry's shape exactly, including `zero_setup: True`.)

No changes needed to `static/index.html`'s provider UI, `app.py`'s upload routes, or `services/transcription.py` — the provider list, model-fetch/default-model persistence, and transcribe call path are all already generic over `PROVIDER_REGISTRY` per the transcription-ux-improvements work that just landed. Adding a new provider to the registry is sufficient for it to appear and function through the existing UI.

## Model management

`SUPPORTED_MODELS = ["tiny", "tiny-streaming", "base", "small-streaming", "medium-streaming"]`, default `"base"`.

On first transcription request for a given model size, the provider must ensure the model files exist locally (Moonshine's docs only document a CLI download path — `python -m moonshine_voice.download --language en` — not a public Python download function). Implementation approach, to be finalized during planning: check for the expected model path first; if absent, invoke the download via `subprocess` (capturing the resulting `model_path`/`model_arch`, or deriving them from the known download-script output convention) rather than shelling out on every request. Cache the resolved `model_path`/`model_arch` per model size for the lifetime of the provider instance (or a session/process-level cache), so repeated transcriptions don't re-check/re-invoke the download step every time.

## Data flow

1. `services/transcription.py` calls `provider.transcribe(audio_path, language=..., temperature=..., **kwargs)` exactly as it does for every other provider — no special-casing needed there.
2. `MoonshineProvider.transcribe()` decodes the audio itself via the `librosa`/`soundfile` dependencies that `moonshine-voice` already pulls in, rather than assuming a specific input format from the chunking pipeline — same self-contained-decode pattern already used for the pyannote torchcodec bypass (`services/diarization.py`).
3. Feeds the decoded audio through the non-streaming transcription path (`transcribe_without_streaming()` or equivalent — exact call confirmed during implementation against the installed package's actual API surface, since public docs describe the shape but not a byte-exact signature).
4. Builds `TranscriptionResult.segments` from Moonshine's line-level `start_time`/`duration` output, matching the existing `Segment` shape other providers produce (start/end/text) — `end` derived as `start_time + duration` since Moonshine doesn't return `end` directly.
5. Returns `TranscriptionResult` with `provider="moonshine"`, `model=<resolved model size>`, `language`, `full_text`, `duration_seconds`, `processing_time` — same fields every other provider returns.

## Error handling

- Missing/failed model download → `ProviderError` with a clear message, matching `builtin.py`'s existing error pattern. No silent fallback to a different model or provider.
- Unsupported model name in config → `ProviderError` listing `SUPPORTED_MODELS`, matching the existing `get_provider()` unknown-provider error shape.
- `moonshine_voice` import failure (package not installed) → `ProviderError` with an actionable "pip install moonshine-voice" message, so an unconfigured environment fails clearly at transcribe-time rather than with an opaque `ImportError` traceback (matches how `services/diarization.py` already handles the optional-dependency case for pyannote).

## Testing / verification

No pytest suite (this repo's established constraint — manual verification only, matching every other task in this project). Manual verification plan:
- Confirm `pip install moonshine-voice` succeeds in the project's `.venv`.
- Transcribe a real short test audio file through the new `moonshine` provider (`base` model), confirm the model auto-downloads on first run and reuses the cached download on a second run.
- Confirm the resulting transcript's segments/text are sane (spot-check against known audio content).
- Confirm `builtin` (faster-whisper) is completely unaffected — its behavior, config, and model list are unchanged by this work.
- Confirm the new provider appears correctly in the existing provider-selection and default-model UI without any frontend changes.

## Documentation

`INSTALL.md` gets a new section (mirroring however the existing `builtin` provider is documented there) explaining: no API key needed, first-use model download behavior, and the English-only model size list.
