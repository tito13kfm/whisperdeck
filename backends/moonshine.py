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

# Process-wide transcriber cache. get_provider() constructs a fresh provider
# instance per call (one per chunk job in the queue), and a Moonshine model
# load is multi-second + multi-GB — without this, every chunk of a chunked
# local run would reload the model. Safe because local chunk dispatch is
# serialized (see services/queue.py concurrency rule for local providers).
_TRANSCRIBER_CACHE: dict = {}


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
            self._resolved_model_name = model_arch_to_string(resolved_arch)
            cached = _TRANSCRIBER_CACHE.get(self._resolved_model_name)
            if cached is None:
                cached = Transcriber(model_path, model_arch=resolved_arch)
                _TRANSCRIBER_CACHE[self._resolved_model_name] = cached
            self._transcriber = cached
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
            # services/diarization.py. The Moonshine C API resamples to its
            # internal 16kHz itself, so the native sample rate is fine here.
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
