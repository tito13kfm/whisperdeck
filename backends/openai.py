"""OpenAI provider — whisper-1 via OpenAI's API."""
import time
import httpx
from .base import BaseProvider, TranscriptionResult, ProviderError


class OpenAIProvider(BaseProvider):
    """Transcribe using OpenAI's Whisper endpoint."""

    API_BASE = "https://api.openai.com/v1"

    def __init__(self, config: dict):
        super().__init__(config)
        self.model = config.get("default_model") or "whisper-1"

    async def transcribe(self, audio_path: str, **kwargs) -> TranscriptionResult:
        if not self.api_key:
            raise ProviderError("OpenAI API key not configured. Set it in Settings > Providers.")

        language = kwargs.get("language", None)
        temperature = kwargs.get("temperature", 0.0)
        response_format = kwargs.get("response_format", "verbose_json")
        prompt = kwargs.get("prompt", "")

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
                if prompt:
                    data["prompt"] = prompt

                response = await client.post(
                    f"{self.API_BASE}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files=files,
                    data=data,
                )

        if response.status_code != 200:
            raise ProviderError(
                f"OpenAI API error ({response.status_code}): {response.text}"
            )

        try:
            result = response.json()
        except ValueError as e:
            raise ProviderError(f"OpenAI returned a non-JSON response: {e}")
        raw_segments = result.get("segments", [])
        duration = max((s.get("end", 0) for s in raw_segments), default=0)

        return TranscriptionResult(
            segments=self._build_segments(raw_segments),
            full_text=result.get("text", ""),
            language=result.get("language") or language or "en",
            duration_seconds=duration,
            model=self.model,
            provider="openai",
            processing_time=time.time() - start_time,
        )

    async def check_health(self) -> dict:
        if not self.api_key:
            return {"ok": False, "error": "No API key configured"}
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                resp = await client.get(
                    f"{self.API_BASE}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                return {"ok": resp.status_code == 200, "error": None if resp.status_code == 200 else resp.text}
        except Exception as e:
            return {"ok": False, "error": str(e)}

    async def list_models(self) -> list[str]:
        """Fetch available models from OpenAI — filter to Whisper models."""
        if not self.api_key:
            return ["whisper-1"]
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self.API_BASE}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if resp.status_code == 200:
                    whisper_models = [
                        m["id"] for m in resp.json().get("data", [])
                        if "whisper" in m.get("id", "").lower()
                    ]
                    return whisper_models if whisper_models else ["whisper-1"]
        except Exception:
            pass
        return ["whisper-1"]