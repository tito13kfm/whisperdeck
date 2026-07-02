# Moonshine Local Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Moonshine as a second, user-selectable local transcription provider alongside `builtin` (faster-whisper), per `docs/superpowers/specs/2026-07-02-moonshine-local-provider-design.md`.

**Architecture:** New `backends/moonshine.py` implementing `BaseProvider`, registered in `backends/__init__.py`'s `PROVIDER_REGISTRY` and `list_providers()`. No frontend/app.py/services changes needed — the provider list and transcribe call path are already generic.

**Tech Stack:** `moonshine-voice` pip package (ships a compiled native DLL; no torch/onnxruntime/librosa dependency — confirmed by inspecting the installed 0.0.63 win_amd64 wheel, correcting the spec's assumption that librosa/onnxruntime were required). `soundfile` (already a project dependency, used identically in `services/diarization.py`) for audio decode.

**Real API surface (traced from the installed package, not docs — the spec flagged this as unconfirmed):**
- `moonshine_voice.string_to_model_arch(name: str) -> ModelArch` — accepts exactly `"tiny"`, `"base"`, `"tiny-streaming"`, `"base-streaming"`, `"small-streaming"`, `"medium-streaming"`.
- `moonshine_voice.model_arch_to_string(arch: ModelArch) -> str` — inverse.
- `moonshine_voice.get_model_for_language(wanted_language="en", wanted_model_arch=<ModelArch>, *, cache_root=None) -> tuple[str, ModelArch]` — downloads model components if not already cached (cache dir via `platformdirs`, overridable with `MOONSHINE_VOICE_CACHE` env var) and returns `(model_path, resolved_arch)`. This replaces the spec's assumed `subprocess`/CLI download step entirely — no subprocess needed. Confirmed `MODEL_INFO["en"]` in `download.py` includes all 5 archs we expose (tiny, tiny-streaming, base, small-streaming, medium-streaming) — every model in `SUPPORTED_MODELS` is actually downloadable.
- `moonshine_voice.Transcriber(model_path: str, model_arch: ModelArch)` — loads the native transcriber.
- `Transcriber.transcribe_without_streaming(audio_data: list[float], sample_rate: int = 16000, flags: int = 0) -> Transcript` — `Transcript.lines: list[TranscriptLine]`, each with `.text`, `.start_time`, `.duration` (no `.end` — matches spec). Confirmed via `mic_transcriber.py:92` comment: "The Moonshine C API resamples to its internal 16 kHz" — the native engine resamples internally, so `sf.read`'s native sample rate can be passed straight through with no separate resample step (no librosa/soxr dependency needed, contra the spec's assumption).

**Spec correction — `app.py` upload route (the spec's Data Flow section claimed no `app.py` changes were needed; this is false and was caught by re-verifying rather than trusting that claim):** `app.py:374` and `app.py:398` both gate on `provider != "builtin"` to decide whether to run `transcode_for_upload` (ffmpeg → 16kHz mono MP3) and whether to chunk large files into background jobs. Both exist to work around upload size limits for *remote* providers; `moonshine` is local like `builtin` and has no such limit. Left as `!= "builtin"`, moonshine would: (a) require ffmpeg on PATH — contradicting its "zero setup" / no-extra-deps positioning, (b) lossily re-encode audio through MP3 before a local model reads it back, and (c) route large local files into the async chunk-job queue instead of transcribing inline, an unnecessary UX change for a provider with no upload constraint. Fix: both conditions become `provider not in LOCAL_PROVIDERS` with `LOCAL_PROVIDERS = ("builtin", "moonshine")` defined near the file's other module-level constants (`app.py:37-47`). (Confirmed no other call sites key off `provider != "builtin"` anywhere else in the codebase — `app.py` is the only place.)

## Global Constraints

- English-only model list: `["tiny", "tiny-streaming", "base", "small-streaming", "medium-streaming"]`, default `"base"` — exact strings, these are also the exact `moonshine_voice` model-arch strings.
- `builtin.py` / faster-whisper must not be modified.
- No pytest suite in this repo (established constraint) — manual verification only, per the spec's "Testing / verification" section.
- `config.get("default_model") or "base"` (the `or`, not `.get`'s default arg) — same bug class already fixed in `groq.py` and used by every other provider's constructor.

---

### Task 1: `backends/moonshine.py` provider + registry wiring

**Files:**
- Create: `backends/moonshine.py`
- Modify: `backends/__init__.py`
- Modify: `app.py:37-47` (add `LOCAL_PROVIDERS` constant), `app.py:374`, `app.py:398`

**Interfaces:**
- Consumes: `BaseProvider`, `TranscriptionResult`, `ProviderError`, `Segment` from `backends/base.py` (`backends/base.py:1-77`, already read — signatures confirmed above).
- Produces: `MoonshineProvider` class, importable as `from .moonshine import MoonshineProvider`; registered under `PROVIDER_REGISTRY["moonshine"]`.

- [x] **Step 1: Write `backends/moonshine.py`**

```python
"""Moonshine local transcription provider — on-device ASR via moonshine-voice.

Runs a Moonshine model locally on CPU. No API key required. Downloads the
model files from Moonshine's CDN on first use of a given model size and
caches them (moonshine_voice manages its own cache dir, overridable via the
MOONSHINE_VOICE_CACHE env var).

Default model: base (58M params, 10.07% WER, English-only).
"""
import time
import asyncio

from .base import BaseProvider, TranscriptionResult, ProviderError


class MoonshineProvider(BaseProvider):
    """Transcribe locally using Moonshine (moonshine-voice), English-only."""

    SUPPORTED_MODELS = [
        "tiny",
        "tiny-streaming",
        "base",
        "small-streaming",
        "medium-streaming",
    ]

    def __init__(self, config: dict):
        super().__init__(config)
        self.model_name = config.get("default_model") or "base"
        self._transcriber = None
        self._resolved_model_name = None

    def _get_transcriber(self):
        """Lazy-load the Moonshine transcriber, downloading the model on first use."""
        if self._transcriber is not None:
            return self._transcriber

        if self.model_name not in self.SUPPORTED_MODELS:
            raise ProviderError(
                f"Unsupported Moonshine model: {self.model_name}. "
                f"Supported models: {self.SUPPORTED_MODELS}"
            )

        try:
            from moonshine_voice import (
                get_model_for_language,
                string_to_model_arch,
                model_arch_to_string,
                Transcriber,
            )
        except ImportError:
            raise ProviderError(
                "moonshine-voice is not installed. Install it with:\n"
                "  pip install moonshine-voice\n\n"
                "This is required for the Moonshine provider."
            )

        try:
            model_arch = string_to_model_arch(self.model_name)
            model_path, resolved_arch = get_model_for_language(
                wanted_language="en", wanted_model_arch=model_arch
            )
            self._transcriber = Transcriber(model_path, model_arch=resolved_arch)
            self._resolved_model_name = model_arch_to_string(resolved_arch)
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"Failed to load Moonshine model '{self.model_name}': {e}")

        return self._transcriber

    async def transcribe(self, audio_path: str, **kwargs) -> TranscriptionResult:
        start_time = time.time()

        try:
            transcriber = self._get_transcriber()
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"Failed to load Moonshine model: {e}")

        try:
            import soundfile as sf

            # Decode audio ourselves rather than assuming a specific input
            # format from the chunking pipeline — same self-contained-decode
            # pattern used for the pyannote torchcodec bypass in
            # services/diarization.py.
            data, sample_rate = sf.read(audio_path, dtype="float32", always_2d=True)
            mono = data.mean(axis=1) if data.shape[1] > 1 else data[:, 0]
            audio_data = mono.tolist()

            loop = asyncio.get_event_loop()

            def _run():
                return transcriber.transcribe_without_streaming(
                    audio_data, sample_rate=sample_rate
                )

            transcript = await loop.run_in_executor(None, _run)

            raw_segments = [
                {
                    "start": line.start_time,
                    "end": line.start_time + line.duration,
                    "text": line.text.strip(),
                    "speaker": None,
                    "confidence": None,
                }
                for line in transcript.lines
            ]

            full_text = " ".join(s["text"] for s in raw_segments)
            duration_seconds = len(audio_data) / sample_rate if sample_rate else 0.0

            return TranscriptionResult(
                segments=self._build_segments(raw_segments),
                full_text=full_text,
                language="en",
                duration_seconds=duration_seconds,
                model=self._resolved_model_name or self.model_name,
                provider="moonshine",
                processing_time=time.time() - start_time,
            )

        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"Moonshine transcription failed: {e}")

    async def list_models(self) -> list[str]:
        return list(self.SUPPORTED_MODELS)

    async def check_health(self) -> dict:
        """Check if moonshine-voice is available."""
        try:
            import moonshine_voice  # noqa
            return {
                "ok": True,
                "model": self.model_name,
            }
        except ImportError:
            return {
                "ok": False,
                "error": "moonshine-voice not installed",
                "fix": "pip install moonshine-voice",
            }
```

- [x] **Step 2: Register in `backends/__init__.py`**

Add the import (after the `builtin` import, `backends/__init__.py:18`):

```python
from .moonshine import MoonshineProvider
```

Add to `PROVIDER_REGISTRY` (`backends/__init__.py:20-27`):

```python
PROVIDER_REGISTRY = {
    "builtin": BuiltinProvider,
    "moonshine": MoonshineProvider,
    "groq": GroqProvider,
    "openai": OpenAIProvider,
    "replicate": ReplicateProvider,
    "local": LocalProvider,
    "openrouter": OpenRouterProvider,
}
```

Add to `list_providers()`'s returned list (`backends/__init__.py:38-90`), immediately after the `"builtin"` dict entry:

```python
        {
            "id": "moonshine",
            "name": "Moonshine",
            "description": "Local · no API key · lightweight on-device ASR",
            "default_model": "base",
            "needs_key": False,
            "key_prefix": "",
            "zero_setup": True,
        },
```

Add `"MoonshineProvider"` to `__all__` (`backends/__init__.py:93-98`):

```python
__all__ = [
    "BaseProvider", "ProviderError",
    "GroqProvider", "OpenAIProvider", "ReplicateProvider", "LocalProvider", "OpenRouterProvider",
    "BuiltinProvider", "MoonshineProvider",
    "get_provider", "list_providers", "PROVIDER_REGISTRY",
]
```

- [x] **Step 3: Treat Moonshine as local in `app.py`'s upload route**

Add near the other module-level path constants (`app.py:37-47`):

```python
LOCAL_PROVIDERS = ("builtin", "moonshine")
```

Change `app.py:374` from:

```python
    if provider != "builtin":
```

to:

```python
    if provider not in LOCAL_PROVIDERS:
```

Change `app.py:398` from:

```python
    if provider != "builtin" and file_size > threshold_bytes:
```

to:

```python
    if provider not in LOCAL_PROVIDERS and file_size > threshold_bytes:
```

- [x] **Step 4: Manual verification — provider loads and appears in the registry**

Run: `.venv\Scripts\python.exe -c "from backends import list_providers, get_provider; print([p['id'] for p in list_providers()]); p = get_provider('moonshine', {}); print(p.model_name)"`

Expected output: a list containing `'moonshine'`, and `base` printed on the next line (no traceback).

- [x] **Step 5: Manual verification — health check**

Run: `.venv\Scripts\python.exe -c "import asyncio; from backends import get_provider; p = get_provider('moonshine', {}); print(asyncio.run(p.check_health()))"`

Expected output: `{'ok': True, 'model': 'base'}` (moonshine-voice is already installed in this venv from the spec investigation).

- [x] **Step 6: Manual verification — real transcription, first run (cold download) and second run (cached)**

Find or record a short (5-10s) English `.wav` test file (reuse any existing sample under the repo, e.g. `moonshine_voice`'s bundled asset at `.venv\Lib\site-packages\moonshine_voice\assets\two_cities.wav` if no project sample exists). Run:

```
.venv\Scripts\python.exe -c "import asyncio; from backends import get_provider; p = get_provider('moonshine', {}); r = asyncio.run(p.transcribe(r'.venv\Lib\site-packages\moonshine_voice\assets\two_cities.wav')); print(r.provider, r.model, r.language); print(r.full_text); print(len(r.segments), 'segments')"
```

Expected: first run prints download progress (or completes silently if `get_model_for_language`'s prefetch already cached it during earlier investigation this session), then prints `moonshine base en`, a non-empty transcript of recognizable English text, and a segment count > 0. Run the exact same command again — expected: no download activity (instant model load from cache), same transcript output.

- [x] **Step 7: Manual verification — MP3 input decodes correctly (moonshine now skips transcode, but must still handle non-WAV uploads users may pass directly to the API/tests)**

Convert the same test wav to MP3 (`ffmpeg -i .venv\Lib\site-packages\moonshine_voice\assets\two_cities.wav two_cities_test.mp3`) and run the same transcribe one-liner against the `.mp3` path. Expected: succeeds, non-empty transcript (confirms `soundfile` 1.2.2's MP3 decode support handles the format even though moonshine no longer goes through `transcode_for_upload`).

- [x] **Step 8: Manual verification — `builtin` provider unaffected**

Run: `.venv\Scripts\python.exe -c "from backends import get_provider; p = get_provider('builtin', {}); print(p.model_name, p.SUPPORTED_MODELS[:3])"`

Expected: `tiny ['tiny', 'tiny.en', 'base']` — unchanged from before this task.

- [x] **Step 9: Manual verification — app.py still imports and starts cleanly**

Run: `.venv\Scripts\python.exe -c "import app"` — expected: no traceback (confirms `LOCAL_PROVIDERS` and the two edited conditionals are syntactically and semantically sound).

- [x] **Step 10: Commit**

```bash
git add backends/moonshine.py backends/__init__.py app.py
git commit -m "feat: add Moonshine local transcription provider"
```

---

### Task 2: `INSTALL.md` documentation

**Files:**
- Modify: `INSTALL.md`

**Interfaces:**
- Consumes: nothing (docs-only).
- Produces: nothing consumed by later tasks.

- [x] **Step 1: Add a Moonshine subsection to `INSTALL.md`'s transcription section**

Insert immediately after the existing "Built-in (Whisper Tiny)" paragraph, inside section `## 3. Transcription — recommended for noisy meetings / heavy accents` (`INSTALL.md:59-63`):

```markdown

Alternatively, for offline/local transcription with meaningfully better
accuracy than Built-in's `tiny` model, switch the Transcribe page provider
to **Moonshine** instead. It runs a small on-device model (English-only)
that beats Whisper Large on word-error-rate at a fraction of the parameter
count, and needs no GPU. Setup:

```
.venv\Scripts\python.exe -m pip install moonshine-voice
```

(not in `requirements.txt`, same as `faster-whisper` — optional, install
only if you want this provider). No API key needed. The model for your
chosen size (`tiny`, `tiny-streaming`, `base` — default, `small-streaming`,
`medium-streaming`) downloads automatically on first transcription and is
cached for subsequent runs.
```

- [x] **Step 2: Manual verification**

Open `INSTALL.md` and confirm the new subsection reads correctly in context (no broken markdown, fits the surrounding section's tone).

- [x] **Step 3: Commit**

```bash
git add INSTALL.md
git commit -m "docs: document Moonshine provider setup in INSTALL.md"
```
