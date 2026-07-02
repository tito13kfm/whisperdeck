"""Groq provider — whisper-large-v3-flash via Groq's API."""
import time
import httpx
from .base import BaseProvider, TranscriptionResult, Segment, ProviderError


class GroqProvider(BaseProvider):
    """Transcribe using Groq's hosted Whisper endpoint."""

    API_BASE = "https://api.groq.com/openai/v1"

    def __init__(self, config: dict):
        super().__init__(config)
        # whisper-large-v3-flash: ~8x faster than full v3, proving equal or
        # better accuracy in practice on this project's meeting audio.
        # `or` (not .get's default arg) so a saved-but-empty default_model in
        # the DB doesn't shadow this fallback with "".
        self.model = config.get("default_model") or "whisper-large-v3-flash"

    async def transcribe(self, audio_path: str, **kwargs) -> TranscriptionResult:
        if not self.api_key:
            raise ProviderError("Groq API key not configured. Set it in Settings > Providers.")

        language = kwargs.get("language", "en")
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
                response = await client.post(
                    f"{self.API_BASE}/audio/transcriptions",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    files=files,
                    data=data,
                )

        if response.status_code != 200:
            raise ProviderError(
                f"Groq API error ({response.status_code}): {response.text}"
            )

        try:
            result = response.json()
        except ValueError as e:
            raise ProviderError(f"Groq returned a non-JSON response: {e}")
        raw_segments = result.get("segments", [])
        duration = max((s.get("end", 0) for s in raw_segments), default=0)

        return TranscriptionResult(
            segments=self._build_segments(raw_segments),
            full_text=result.get("text", ""),
            language=result.get("language") or language,
            duration_seconds=duration,
            model=self.model,
            provider="groq",
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
        """Fetch available transcription models from Groq."""
        if not self.api_key:
            return ["whisper-large-v3-flash", "whisper-large-v3", "whisper-large-v3-turbo", "distil-whisper-large-v3"]
        try:
            async with httpx.AsyncClient(timeout=15) as client:
                resp = await client.get(
                    f"{self.API_BASE}/models",
                    headers={"Authorization": f"Bearer {self.api_key}"},
                )
                if resp.status_code == 200:
                    models = [
                        m["id"] for m in resp.json().get("data", [])
                        if "whisper" in m.get("id", "").lower()
                    ]
                    return models if models else ["whisper-large-v3"]
        except Exception:
            pass
        return ["whisper-large-v3-flash", "whisper-large-v3", "whisper-large-v3-turbo", "distil-whisper-large-v3"]