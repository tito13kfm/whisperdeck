"""OpenRouter provider — access Whisper models via OpenRouter's unified API.

OpenRouter provides OpenAI-compatible endpoints for many models including
openai/whisper-1 and community-hosted Whisper variants.
"""
import time
import httpx
from .base import BaseProvider, TranscriptionResult, ProviderError


class OpenRouterProvider(BaseProvider):
    """Transcribe using OpenRouter's unified API."""

    API_BASE = "https://openrouter.ai/api/v1"

    def __init__(self, config: dict):
        super().__init__(config)
        self.model = config.get("default_model", "openai/whisper-1")
        self.api_key = config.get("api_key", "")
        self.site_url = config.get("site_url", "")
        self.site_name = config.get("site_name", "")

    async def transcribe(self, audio_path: str, **kwargs) -> TranscriptionResult:
        if not self.api_key:
            raise ProviderError("OpenRouter API key not configured. Set it in Settings > Providers.")

        language = kwargs.get("language", None)
        temperature = kwargs.get("temperature", 0.0)
        response_format = kwargs.get("response_format", "verbose_json")

        start_time = time.time()

        async with httpx.AsyncClient(timeout=300) as client:
            with open(audio_path, "rb") as f:
                files = {"file": (audio_path, f, "audio/mpeg")}
                data = {
                    "model": self.model,
                    "temperature": temperature,
                    "response_format": response_format,
                }
                if language and language != "auto":
                    data["language"] = language

                headers = {
                    "Authorization": f"Bearer {self.api_key}",
                }
                if self.site_url:
                    headers["HTTP-Referer"] = self.site_url
                if self.site_name:
                    headers["X-Title"] = self.site_name

                response = await client.post(
                    f"{self.API_BASE}/audio/transcriptions",
                    headers=headers,
                    files=files,
                    data=data,
                )

        if response.status_code == 404:
            # OpenRouter may route differently — try completions-based approach
            # or return a clear error
            raise ProviderError(
                f"OpenRouter: model '{self.model}' may not support audio transcription. "
                f"Try 'openai/whisper-1'. Response ({response.status_code}): {response.text}"
            )
        if response.status_code != 200:
            raise ProviderError(
                f"OpenRouter API error ({response.status_code}): {response.text}"
            )

        result = response.json()
        raw_segments = result.get("segments", [])

        # Some OpenRouter models return flat text without segments
        if not raw_segments and result.get("text"):
            return TranscriptionResult(
                full_text=result["text"],
                language=language or "en",
                duration_seconds=0,
                model=self.model,
                provider="openrouter",
                processing_time=time.time() - start_time,
            )

        duration = max((s.get("end", 0) for s in raw_segments), default=0)

        return TranscriptionResult(
            segments=self._build_segments(raw_segments),
            full_text=result.get("text", ""),
            language=language or "en",
            duration_seconds=duration,
            model=self.model,
            provider="openrouter",
            processing_time=time.time() - start_time,
        )

    async def list_models(self) -> list[str]:
        """Fetch available transcription-capable models from OpenRouter."""
        if not self.api_key:
            return self._default_models()

        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self.API_BASE}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if resp.status_code == 200:
                    data = resp.json()
                    models = []
                    for m in data.get("data", []):
                        mid = m.get("id", "")
                        # Filter to likely transcription models (Whisper variants)
                        if "whisper" in mid.lower() or "transcribe" in mid.lower():
                            models.append(mid)
                    # Also include common known models
                    known = self._default_models()
                    for k in known:
                        if k not in models:
                            models.append(k)
                    return models if models else known
        except Exception:
            pass
        return self._default_models()

    def _default_models(self) -> list[str]:
        return [
            "openai/whisper-1",
            "deepgram/whisper-large-v3-turbo",
            "groq/whisper-large-v3-turbo",
        ]

    async def check_health(self) -> dict:
        if not self.api_key:
            return {"ok": False, "error": "No API key configured"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.API_BASE}/auth/key",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                return {
                    "ok": resp.status_code == 200,
                    "error": None if resp.status_code == 200 else resp.text,
                }
        except Exception as e:
            return {"ok": False, "error": str(e)}