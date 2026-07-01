"""Local provider — OpenAI-compatible endpoint (Whisper.cpp, Ollama, LocalAI)."""
import time
import httpx
from .base import BaseProvider, TranscriptionResult, ProviderError


class LocalProvider(BaseProvider):
    """Transcribe using a local OpenAI-compatible endpoint.

    Supports Whisper.cpp server, LocalAI, Ollama, or any OpenAI-compatible
    transcription endpoint running locally.
    """

    def __init__(self, config: dict):
        super().__init__(config)
        self.api_url = config.get("api_url", "http://localhost:8080/v1")
        self.model = config.get("default_model") or "whisper-large-v3-turbo"
        # Strip trailing slash
        self.api_url = self.api_url.rstrip("/")

    async def transcribe(self, audio_path: str, **kwargs) -> TranscriptionResult:
        language = kwargs.get("language", "en")
        temperature = kwargs.get("temperature", 0.0)
        response_format = kwargs.get("response_format", "verbose_json")

        start_time = time.time()

        async with httpx.AsyncClient(timeout=600) as client:
            with open(audio_path, "rb") as f:
                files = {"file": (audio_path, f, "audio/mpeg")}
                data = {
                    "model": self.model,
                    "temperature": temperature,
                    "response_format": response_format,
                }
                if language and language != "auto":
                    data["language"] = language

                headers = {}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"

                response = await client.post(
                    f"{self.api_url}/audio/transcriptions",
                    headers=headers,
                    files=files,
                    data=data,
                )

        if response.status_code != 200:
            raise ProviderError(
                f"Local endpoint error ({response.status_code}): {response.text}"
            )

        try:
            result = response.json()
        except ValueError as e:
            raise ProviderError(f"Local endpoint returned a non-JSON response: {e}")
        raw_segments = result.get("segments", [])

        # Some local endpoints return a flat text response
        if not raw_segments and result.get("text"):
            return TranscriptionResult(
                full_text=result["text"],
                language=result.get("language") or language,
                duration_seconds=0,
                model=self.model,
                provider="local",
                processing_time=time.time() - start_time,
            )

        duration = max((s.get("end", 0) for s in raw_segments), default=0)

        return TranscriptionResult(
            segments=self._build_segments(raw_segments),
            full_text=result.get("text", ""),
            language=result.get("language") or language,
            duration_seconds=duration,
            model=self.model,
            provider="local",
            processing_time=time.time() - start_time,
        )

    async def check_health(self) -> dict:
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self.api_url}/models")
                return {
                    "ok": resp.status_code == 200,
                    "error": None if resp.status_code == 200 else resp.text,
                    "url": self.api_url,
                }
        except Exception as e:
            return {"ok": False, "error": str(e), "url": self.api_url}

    async def list_models(self) -> list[str]:
        """Fetch models from a local OpenAI-compatible endpoint."""
        try:
            async with httpx.AsyncClient(timeout=10) as client:
                headers = {}
                if self.api_key:
                    headers["Authorization"] = f"Bearer {self.api_key}"
                resp = await client.get(
                    f"{self.api_url}/models", headers=headers
                )
                if resp.status_code == 200:
                    models = [
                        m["id"] for m in resp.json().get("data", [])
                    ]
                    # Filter to Whisper if there are many, otherwise return all
                    whisper_only = [m for m in models if "whisper" in m.lower()]
                    return whisper_only if whisper_only else models[:20]
        except Exception:
            pass
        return ["whisper-large-v3-turbo", "whisper-large-v3"]