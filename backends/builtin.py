"""Built-in local transcription provider using faster-whisper.

Runs Whisper models locally on CPU (or GPU if available). No API key required.
Automatically downloads the model from HuggingFace on first use.

Default model: tiny (~75 MB, very fast on CPU, great for quick dictation).
"""
import time
import os
from typing import Optional

from .base import BaseProvider, TranscriptionResult, ProviderError

# See _get_model — avoids reloading WhisperModel for every chunk job.
_MODEL_CACHE: dict = {}


class BuiltinProvider(BaseProvider):
    """Transcribe locally using faster-whisper with a downloaded Whisper model.

    The model is downloaded from HuggingFace on first use and cached in
    ~/.cache/huggingface/hub/ (or the WHISPER_CACHE_DIR env var).
    """

    SUPPORTED_MODELS = [
        "tiny",
        "tiny.en",
        "base",
        "base.en",
        "small",
        "small.en",
        "medium",
        "medium.en",
        "large-v1",
        "large-v2",
        "large-v3",
        "large-v3-turbo",
        "distil-small.en",
        "distil-medium.en",
        "distil-large-v2",
        "distil-large-v3",
    ]

    def __init__(self, config: dict):
        super().__init__(config)
        self.model_name = config.get("default_model") or "tiny"
        self.device = config.get("device", "auto")  # "auto", "cpu", "cuda"
        self.compute_type = config.get("compute_type", "default")  # "default", "float16", "int8_float16"
        self.cache_dir = os.environ.get(
            "WHISPER_CACHE_DIR",
            os.path.expanduser("~/.cache/whisper"),
        )
        self._model = None

    def _get_model(self):
        """Lazy-load the Whisper model."""
        if self._model is not None:
            return self._model

        try:
            from faster_whisper import WhisperModel
        except ImportError:
            raise ProviderError(
                "faster-whisper is not installed. Install it with:\n"
                "  pip install faster-whisper\n\n"
                "This is required for the Built-in (Whisper Tiny) provider."
            )

        # Determine device
        device = self.device
        if device == "auto":
            try:
                import torch
                device = "cuda" if torch.cuda.is_available() else "cpu"
            except ImportError:
                device = "cpu"

        # Determine compute type
        compute = self.compute_type
        if compute == "default":
            compute = "float16" if device == "cuda" else "int8"

        os.makedirs(self.cache_dir, exist_ok=True)

        # Process-wide cache — get_provider() makes a fresh provider per chunk
        # job, and a WhisperModel load is expensive. Keyed by everything that
        # changes the loaded artifact. Serial local dispatch (services/queue.py)
        # keeps the shared instance uncontended.
        cache_key = (self.model_name, device, compute)
        cached = _MODEL_CACHE.get(cache_key)
        if cached is None:
            cached = WhisperModel(
                self.model_name,
                device=device,
                compute_type=compute,
                download_root=self.cache_dir,
                cpu_threads=os.cpu_count() or 4,
                num_workers=2,
            )
            _MODEL_CACHE[cache_key] = cached
        self._model = cached
        return self._model

    async def transcribe(self, audio_path: str, **kwargs) -> TranscriptionResult:
        language = kwargs.get("language", None)
        temperature = kwargs.get("temperature", 0.0)
        initial_prompt = kwargs.get("prompt", "")
        vad_filter = kwargs.get("vad_filter", True)

        start_time = time.time()

        try:
            model = self._get_model()
        except ProviderError:
            raise
        except Exception as e:
            raise ProviderError(f"Failed to load Whisper model: {e}")

        try:
            # Run transcription (blocking — run in thread pool)
            import asyncio
            loop = asyncio.get_event_loop()

            def _run():
                segs, info = model.transcribe(
                    audio_path,
                    language=language if language and language != "auto" else None,
                    temperature=temperature,
                    initial_prompt=initial_prompt or None,
                    vad_filter=vad_filter,
                    beam_size=5,
                    word_timestamps=True,
                )
                return list(segs), info

            segments, info = await loop.run_in_executor(None, _run)

            detected_language = info.language if info else (language or "en")
            duration = info.duration if info else 0

            raw_segments = [
                {
                    "start": s.start,
                    "end": s.end,
                    "text": s.text.strip(),
                    "speaker": None,
                    "confidence": s.avg_logprob if hasattr(s, "avg_logprob") else None,
                }
                for s in segments
            ]

            full_text = " ".join(s["text"] for s in raw_segments)

            return TranscriptionResult(
                segments=self._build_segments(raw_segments),
                full_text=full_text,
                language=detected_language,
                duration_seconds=duration,
                model=self.model_name,
                provider="builtin",
                processing_time=time.time() - start_time,
            )

        except Exception as e:
            raise ProviderError(f"Local transcription failed: {e}")

    async def list_models(self) -> list[str]:
        return list(self.SUPPORTED_MODELS)

    async def check_health(self) -> dict:
        """Check if faster-whisper is available."""
        try:
            import faster_whisper  # noqa
            return {
                "ok": True,
                "model": self.model_name,
                "cache_dir": self.cache_dir,
            }
        except ImportError:
            return {
                "ok": False,
                "error": "faster-whisper not installed",
                "fix": "pip install faster-whisper",
            }