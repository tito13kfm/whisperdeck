"""Replicate provider — whisper-large-v3-turbo via Replicate's API."""
import time
import asyncio
import httpx
import json
from .base import BaseProvider, TranscriptionResult, ProviderError


class ReplicateProvider(BaseProvider):
    """Transcribe using Replicate's hosted Whisper model."""

    API_BASE = "https://api.replicate.com/v1"
    MODEL_VERSION = "varunp2k/whisper-large-v3-turbo:latest"

    def __init__(self, config: dict):
        super().__init__(config)
        self.model = config.get("default_model", "whisper-large-v3-turbo")
        model_ref = config.get("model_ref", "")
        if model_ref:
            self.MODEL_VERSION = model_ref

    async def transcribe(self, audio_path: str, **kwargs) -> TranscriptionResult:
        if not self.api_key:
            raise ProviderError("Replicate API key not configured. Set it in Settings > Providers.")

        language = kwargs.get("language", "en")
        temperature = kwargs.get("temperature", 0.0)

        start_time = time.time()

        # Upload or provide file URL — Replicate needs a URL, so we'd need to host
        # For the prototype, we use a data URL scheme or require the user to provide a URL.
        # In production, upload to a temporary hosting service or use a local approach.
        # For now, we'll read the file and pass it as a base64 data URI if small enough,
        # or use file.io / a temporary upload.

        import os
        file_size = os.path.getsize(audio_path)
        if file_size > 10 * 1024 * 1024:  # 10MB limit for data URI
            raise ProviderError(
                "File too large for Replicate data-URI approach (>10MB). "
                "Please provide a publicly accessible URL to the audio file, "
                "or use Groq/OpenAI which accept direct file uploads."
            )

        import base64
        with open(audio_path, "rb") as f:
            audio_b64 = base64.b64encode(f.read()).decode("utf-8")
            audio_uri = f"data:audio/mpeg;base64,{audio_b64}"

        async with httpx.AsyncClient(timeout=600) as client:
            # Create prediction
            resp = await client.post(
                f"{self.API_BASE}/predictions",
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                json={
                    "version": self.MODEL_VERSION,
                    "input": {
                        "audio": audio_uri,
                        "language": language if language != "auto" else None,
                        "temperature": temperature,
                        "return_timestamps": True,
                    },
                },
            )

            if resp.status_code != 201:
                raise ProviderError(
                    f"Replicate API error ({resp.status_code}): {resp.text}"
                )

            prediction = resp.json()
            prediction_id = prediction["id"]

            # Poll until complete
            while prediction["status"] not in ("succeeded", "failed", "canceled"):
                await asyncio.sleep(2)
                resp = await client.get(
                    f"{self.API_BASE}/predictions/{prediction_id}",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                prediction = resp.json()

            if prediction["status"] != "succeeded":
                raise ProviderError(
                    f"Replicate prediction failed: {prediction.get('error', 'unknown error')}"
                )

        output = prediction.get("output", {})
        raw_segments = []
        if isinstance(output, dict):
            raw_segments = output.get("segments", output.get("chunks", []))
        elif isinstance(output, str):
            # Text-only output
            return TranscriptionResult(
                full_text=output,
                language=language,
                duration_seconds=0,
                model=self.model,
                provider="replicate",
                processing_time=time.time() - start_time,
            )

        duration = max((s.get("end", 0) for s in raw_segments), default=0)
        full_text = " ".join(s.get("text", "") for s in raw_segments)

        return TranscriptionResult(
            segments=self._build_segments(raw_segments),
            full_text=full_text.strip(),
            language=language,
            duration_seconds=duration,
            model=self.model,
            provider="replicate",
            processing_time=time.time() - start_time,
        )

    async def check_health(self) -> dict:
        if not self.api_key:
            return {"ok": False, "error": "No API key configured"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.API_BASE}/models/varunp2k/whisper-large-v3-turbo",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                return {"ok": resp.status_code == 200, "error": None if resp.status_code == 200 else resp.text}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def list_models(self) -> list[str]:
        """Return known Whisper models available on Replicate."""
        # Replicate doesn't have a simple model list filtered by capability,
        # so return the well-known versions.
        return [
            "varunp2k/whisper-large-v3-turbo",
            "openai/whisper",
            "vaibhavs10/incredibly-fast-whisper",
        ]