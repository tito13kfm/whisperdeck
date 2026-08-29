"""OpenRouter provider — access Whisper, Nova-3, and other STT models via OpenRouter.

OpenRouter provides OpenAI-compatible endpoints for many models including
openai/whisper-1, deepgram/nova-3, and community-hosted Whisper variants.

Note: when proxying OpenAI's gpt-transcribe, OpenRouter strips the
languages array and flattens usage to {seconds, cost} — see
docs/superpowers/specs/2026-08-06-gpt-transcribe-provider-and-hotword-context-design.md.
Direct openai provider is the full-fidelity path for that model.
"""
import time
import httpx
from services.hotwords import sanitize_keywords
from .base import BaseProvider, TranscriptionResult, ProviderError


def _is_transcribe_family(model: str) -> bool:
    return "transcribe" in (model or "").lower()


class OpenRouterProvider(BaseProvider):
    """Transcribe using OpenRouter's unified API."""

    API_BASE = "https://openrouter.ai/api/v1"

    def __init__(self, config: dict):
        super().__init__(config)
        self.model = config.get("default_model") or "openai/whisper-1"
        self.api_key = config.get("api_key", "")
        self.site_url = config.get("site_url", "")
        self.site_name = config.get("site_name", "")

    async def transcribe(self, audio_path: str, **kwargs) -> TranscriptionResult:
        if not self.api_key:
            raise ProviderError("OpenRouter API key not configured. Set it in Settings > Providers.")

        language = kwargs.get("language", None)
        temperature = kwargs.get("temperature", 0.0)
        response_format = kwargs.get("response_format", "verbose_json")
        prompt = kwargs.get("prompt", "")
        keywords = kwargs.get("keywords", None)
        languages = kwargs.get("languages", None)

        if _is_transcribe_family(self.model) and response_format == "verbose_json":
            response_format = "json"

        start_time = time.time()

        async with httpx.AsyncClient(timeout=300) as client:
            with open(audio_path, "rb") as f:
                files = {"file": (audio_path, f, "audio/mpeg")}
                data: dict = {
                    "model": self.model,
                    "temperature": temperature,
                    "response_format": response_format,
                }
                if _is_transcribe_family(self.model):
                    if keywords is not None:
                        sanitized = sanitize_keywords(list(keywords) if isinstance(keywords, (list, tuple)) else [str(keywords)])
                        if sanitized:
                            data["keywords[]"] = sanitized
                    if languages is not None:
                        langs = list(languages) if isinstance(languages, (list, tuple)) else [str(languages)]
                        langs = [str(x).strip() for x in langs if str(x).strip()]
                        if langs:
                            data["languages[]"] = langs
                        elif language and language != "auto":
                            data["languages[]"] = [language]
                    elif language and language != "auto":
                        data["languages[]"] = [language]
                else:
                    if language and language != "auto":
                        data["language"] = language
                if prompt:
                    data["prompt"] = prompt

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
            raise ProviderError(
                f"OpenRouter: model '{self.model}' may not support audio transcription. "
                f"Try 'openai/whisper-1'. Response ({response.status_code}): {response.text}"
            )
        if response.status_code != 200:
            raise ProviderError(
                f"OpenRouter API error ({response.status_code}): {response.text}"
            )

        try:
            result = response.json()
        except ValueError as e:
            raise ProviderError(f"OpenRouter returned a non-JSON response: {e}")
        raw_segments = result.get("segments", [])

        if not raw_segments and result.get("text"):
            if _is_transcribe_family(self.model):
                duration = result.get("usage", {}).get("seconds", 0) or 0
                if not duration and isinstance(result.get("usage"), dict):
                    duration = result["usage"].get("seconds", 0) or 0
                langs_resp = result.get("languages")
                if isinstance(langs_resp, list) and langs_resp:
                    first = langs_resp[0]
                    if isinstance(first, dict):
                        det_lang = first.get("code") or first.get("language") or language or "en"
                    elif isinstance(first, str):
                        det_lang = first
                    else:
                        det_lang = language or "en"
                else:
                    det_lang = result.get("language") or language or "en"
            else:
                duration = 0
                det_lang = result.get("language") or language or "en"
            return TranscriptionResult(
                full_text=result["text"],
                language=det_lang,
                duration_seconds=duration,
                model=self.model,
                provider="openrouter",
                processing_time=time.time() - start_time,
            )

        if raw_segments:
            duration = max((s.get("end", 0) for s in raw_segments), default=0)
        else:
            duration = result.get("usage", {}).get("seconds", 0) or 0

        if _is_transcribe_family(self.model):
            langs_resp = result.get("languages")
            if isinstance(langs_resp, list) and langs_resp:
                first = langs_resp[0]
                if isinstance(first, dict):
                    det_lang = first.get("code") or first.get("language") or language or "en"
                elif isinstance(first, str):
                    det_lang = first
                else:
                    det_lang = language or "en"
            else:
                det_lang = result.get("language") or language or "en"
        else:
            det_lang = result.get("language") or language or "en"

        return TranscriptionResult(
            segments=self._build_segments(raw_segments),
            full_text=result.get("text", ""),
            language=det_lang,
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
                        if any(kw in mid.lower() for kw in ["whisper", "transcribe", "nova", "deepgram"]):
                            models.append(mid)
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
            "openai/gpt-transcribe",
            "deepgram/nova-3",
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
